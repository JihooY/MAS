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
        return sha256(json.dumps([self.settings.ai_url, self.settings.ai_model]).encode()).hexdigest()

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

    def submit(self, project, items, start, end, ai, enqueue=False):
        # Include measured evidence so only identical pending work is coalesced.
        payload = {"project_id": project["id"], "items": items, "start": start, "end": end,
                   "ai": ai, "model": self.settings.ai_model if ai else None,
                   "provider": self.settings.ai_url if ai else None}
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
                      if k not in {"evidence", "fingerprint", "provider_signature", "start", "end", "result"}}
            waiting = sorted((j for j in self.jobs.values() if j["status"] == "queued"),
                             key=lambda j: j["created_at"])
            result["queue_position"] = next((i + 1 for i, j in enumerate(waiting) if j["id"] == job_id), 0)
            if include_result and job.get("result"):
                result["result"] = deepcopy(job["result"])
                result["result"]["content_html"] = render_markdown(result["result"]["content"])
            return result

    def snapshot(self):
        with self.lock:
            ordered = sorted(self.jobs.values(), key=lambda j: j["created_at"])
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
                raise ValueError("Queued AI provider configuration changed; submit a new request.")
            content = (ai_report(self.settings, job["evidence"]) if job["kind"] == "ai"
                       else measured_report(job["evidence"], job["start"], job["end"]))
            result = {"job_id": job_id, "content": content, "content_html": render_markdown(content),
                      "evidence": job["evidence"], "kind": job["kind"], "model": job["model"], "created_at": now()}
            with self.lock:
                project = self.store.get(job["project_id"])
                if project is not None:
                    project["reports"] = (project.get("reports", []) + [result])[-10:]
                    self.store.save(project)
                self._update(job, status="done", progress=100, message="리포트 분석 완료", result=result)
        except Exception:
            log.exception("Report failed: %s", job_id)
            with self.lock:
                self._update(job, status="failed", progress=None,
                             message="AI 제공자 연결·인증·응답 오류입니다. 연결 설정을 확인하고 다시 요청하세요."
                             if job["kind"] == "ai" else "측정 리포트 생성에 실패했습니다. 서버 로그를 확인하세요.")
