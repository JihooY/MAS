"""Server-owned, durable FIFO report queue for the single studio server."""
from copy import deepcopy
from datetime import datetime, timezone
from hashlib import sha256
import json
import logging
from uuid import uuid4

from fastapi import HTTPException

from .markdown import render_markdown
from .reports import ai_report, measured_report
from .ai_provider import AIError, Provider, redact
from .music_ai import PIPELINE_VERSION, audio_inputs, listen

log = logging.getLogger(__name__)
PENDING = {"queued", "processing"}


def now():
    return datetime.now(timezone.utc).isoformat()


class ReportQueue:
    def __init__(self, store, settings, pool):
        self.store, self.settings, self.pool = store, settings, pool
        self.jobs = {}
        # Admission and project mutation share a lock to avoid cross-browser races.
        self.lock = store.lock

    def _provider_signature(self):
        s = self.settings
        return sha256(json.dumps([PIPELINE_VERSION, s.audio_ai_url, s.audio_ai_model, s.audio_ai_api_key,
                                  s.llm_ai_url, s.llm_ai_model, s.llm_ai_api_key,
                                  s.audio_clip_seconds, s.audio_max_tokens, s.llm_max_tokens]).encode()).hexdigest()

    def recover(self):
        with self.lock:
            self.jobs = {j["id"]: j for j in self.store.report_jobs()}
            for job in sorted(self.jobs.values(), key=lambda j: j["created_at"]):
                if job["status"] == "processing":
                    self._update(job, status="failed", progress=None,
                                 message="서버 재시작으로 분석이 중단되었습니다. 다시 요청하세요.")
                elif job["status"] == "queued":
                    self.pool.submit(self._work, job["id"])

    def _update(self, job, **values):
        job.update(**values, updated_at=now())
        self.store.save_report_job(job)

    def has_pending(self, project_id=None):
        with self.lock:
            return any(j["status"] in PENDING and (project_id is None or j["project_id"] == project_id)
                       for j in self.jobs.values())

    def submit(self, project, items, start, end, ai, enqueue=False, language="ko"):
        language = "en" if language == "en" else "ko"
        # Include measured evidence so only identical pending work is coalesced.
        inputs = []
        if ai:
            try:
                Provider.from_settings(self.settings, "AUDIO")
                Provider.from_settings(self.settings, "LLM")
                inputs = audio_inputs(self.settings, project, items)
            except AIError as exc:
                raise HTTPException(409, redact(self.settings, exc)) from None
        payload = {"project_id": project["id"], "items": items, "start": start, "end": end,
                   "ai": ai, "inputs": inputs,
                   "model": self.settings.llm_ai_model if ai else None,
                   "provider_signature": self._provider_signature() if ai else None}
        # Language belongs to the immutable request, including after restart.
        if language == "en":
            payload["language"] = language
        fingerprint = sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
        with self.lock:
            for job in self.jobs.values():
                if job["status"] in PENDING and job["fingerprint"] == fingerprint:
                    return {**self.get(job["id"]), "reused": True}
            if self.has_pending() and not enqueue:
                raise HTTPException(409, "이미 리포트 분석이 진행 중입니다. 대기열에 추가해 주세요.")
            if sum(j["status"] in PENDING for j in self.jobs.values()) >= 20:
                raise HTTPException(409, "대기열이 가득 찼습니다. 작업 완료 후 다시 추가하세요.")
            stamp = now()
            job = {"id": uuid4().hex, "project_id": project["id"], "project_name": project["name"],
                   "source_ids": list(dict.fromkeys(i["source_id"] for i in items)),
                   "vocal_ids": [i["track_id"] for i in items], "task": "report",
                   "kind": "ai" if ai else "measured", "model": payload["model"],
                   "audio_model": self.settings.audio_ai_model if ai else None,
                   "pipeline_version": PIPELINE_VERSION if ai else None,
                   "audio_inputs": inputs,
                   "language": language,
                   "provider_signature": self._provider_signature() if ai else None,
                   "fingerprint": fingerprint, "evidence": deepcopy(items), "start": start, "end": end,
                   "status": "queued", "progress": 0, "message": "리포트 분석 대기",
                   "created_at": stamp, "updated_at": stamp}
            self.store.save_report_job(job)
            self.jobs[job["id"]] = job
            self.pool.submit(self._work, job["id"])
            return self.get(job["id"])

    def get(self, job_id, include_result=True):
        with self.lock:
            job = self.jobs.get(job_id)
            if not job:
                raise HTTPException(404, "작업 정보를 찾을 수 없습니다.")
            result = {k: deepcopy(v) for k, v in job.items()
                      if k not in {"evidence", "audio_inputs", "audio_analyses", "fingerprint", "provider_signature", "start", "end", "result"}}
            waiting = sorted((j for j in self.jobs.values() if j["status"] == "queued"),
                             key=lambda j: j["created_at"])
            result["queue_position"] = next((i + 1 for i, j in enumerate(waiting) if j["id"] == job_id), 0)
            if include_result and job.get("result"):
                result["result"] = deepcopy(job["result"])
                result["result"]["content_html"] = render_markdown(result["result"]["content"])
            if include_result and job.get("audio_analyses") and not job.get("result"):
                result["partial_audio_analyses"] = deepcopy(job["audio_analyses"])
            return result

    def snapshot(self, project_ids=None):
        with self.lock:
            ordered = sorted((j for j in self.jobs.values()
                              if project_ids is None or j["project_id"] in project_ids),
                             key=lambda j: j["created_at"])
            return {"busy": self.has_pending(),
                    "jobs": [self.get(j["id"], False) for j in ordered if j["status"] in PENDING],
                    "recent": [self.get(j["id"], False) for j in ordered if j["status"] not in PENDING][-50:]}

    def forget(self, project_id, source_id=None):
        with self.lock:
            for jid in [j["id"] for j in self.jobs.values() if j["project_id"] == project_id
                        and (source_id is None or source_id in j["source_ids"])]:
                self.store.delete_report_job(jid)
                del self.jobs[jid]

    def _work(self, job_id):
        with self.lock:
            job = self.jobs[job_id]
            self._update(job, status="processing", started_at=now(), progress=None,
                         message="AI 제공자 응답 대기" if job["kind"] == "ai" else "측정 리포트 작성 중")
        try:
            if job["kind"] == "ai" and job.get("provider_signature") != self._provider_signature():
                raise AIError("Queued AI provider configuration changed; submit a new request.")
            # Persist the submitting language so restart or later UI switches cannot change a queued report.
            options = {"language": "en"} if job.get("language") == "en" else {}
            observations, validation, completion = [], {}, {}
            if job["kind"] == "ai":
                if job.get("pipeline_version") != PIPELINE_VERSION or not job.get("audio_inputs"):
                    raise AIError("This queued request predates audio listening. Submit a new request.")
                for role in ("AUDIO", "LLM"):
                    with self.lock:
                        self._update(job, stage="model_check", message="서빙 모델 ID 확인 중")
                    validation[role] = Provider.from_settings(self.settings, role).check_model(self.settings)
                    with self.lock:
                        self._update(job, validation=deepcopy(validation))
                with self.lock:
                    self._update(job, stage="listening", message="Music Flamingo 실제 오디오 분석 중")
                def save_observations(values):
                    with self.lock:
                        self._update(job, audio_analyses=deepcopy(values),
                                     message=f"Music Flamingo 오디오 구간 {len(values)}개 분석 완료")
                observations = listen(self.settings, Provider.from_settings(self.settings, "AUDIO"),
                                      job["audio_inputs"], job.get("language", "ko"), save_observations)
                validation["AUDIO"]["audio_input_verified"] = True
                validation["AUDIO"]["input_format"] = "input_audio/base64 WAV PCM_16 mono 24000Hz"
                with self.lock:
                    self._update(job, stage="interpreting", validation=deepcopy(validation),
                                 message="gpt-oss 최종 보고서 생성 중")
                completion = ai_report(self.settings, job["evidence"], audio_analyses=observations, **options)
                content = completion["content"]
                validation["LLM"]["text_generation_verified"] = True
            else:
                content = measured_report(job["evidence"], job["start"], job["end"], **options)
            result = {"job_id": job_id, "content": content, "content_html": render_markdown(content),
                      "evidence": job["evidence"], "kind": job["kind"], "model": job["model"],
                      "language": job.get("language", "ko"), "created_at": now()}
            if job["kind"] == "ai":
                result.update(pipeline_version=PIPELINE_VERSION, audio_model=job["audio_model"],
                              audio_analyses=observations, validation=validation, expert_feedback=[],
                              llm_response={k: v for k, v in completion.items() if k != "content"})
            with self.lock:
                project = self.store.get(job["project_id"])
                if project is not None:
                    project["reports"] = (project.get("reports", []) + [result])[-10:]
                    self.store.save(project)
                self._update(job, status="done", progress=100, message="리포트 분석 완료", result=result)
        except Exception as exc:
            message = (redact(self.settings, exc) if isinstance(exc, AIError) else
                       "Unexpected analysis error; check server logs and selected audio files.")
            # Never log request bodies, headers, base64 audio, or exception reprs with credentials.
            log.error("Report failed: %s stage=%s type=%s message=%s", job_id,
                      job.get("stage", "report"), type(exc).__name__, message)
            with self.lock:
                self._update(job, status="failed", progress=None,
                             message=message
                             if job["kind"] == "ai" else "측정 리포트 생성에 실패했습니다. 서버 로그를 확인하세요.")
