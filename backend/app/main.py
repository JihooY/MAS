import json
import logging
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit
from uuid import uuid4
from typing import Literal

from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field
from starlette.exceptions import HTTPException as StarletteHTTPException

from . import audio, separation
from .markdown import render_markdown
from .project import upgrade_project
from .config import Settings
from .database import Store
from .reports import evidence
from .ai_provider import configured
from .report_queue import ReportQueue
from .ui import register_ui
from .auth import Auth, register_auth
from .i18n import get_language, register_language, translate

log = logging.getLogger(__name__)


def now():
    return datetime.now(timezone.utc).isoformat()


class ProjectInput(BaseModel):
    name: str = Field(min_length=1, max_length=120)


class TrackInput(BaseModel):
    offset: float = Field(default=0, ge=-120, le=120)


class TransformInput(BaseModel):
    semitones: float = Field(default=0, ge=-12, le=12)
    speed: float = Field(default=1, ge=0.5, le=2)
    normalize: bool = True
    vocal_id: str | None = None


class ReportInput(BaseModel):
    start: float = Field(default=0, ge=0)
    end: float = Field(gt=0)
    ai: bool = False
    enqueue: bool = False
    source_ids: list[str] | None = None
    vocal_id: str | None = None


class SeparationInput(BaseModel):
    model: Literal["htdemucs", "htdemucs_6s"] = "htdemucs"


class VocalInput(BaseModel):
    vocal_id: str | None = None


def create_app(settings=None):
    s = settings or Settings()
    s.prepare()
    store = Store(s)
    auth = Auth(store, s)
    jobs = {}
    pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="vocal-worker")
    report_queue = ReportQueue(store, s, pool)

    @asynccontextmanager
    async def lifespan(app):
        store.connect()
        if not store.error:
            auth.initialize()
            for project in store.list():
                upgrade_project(project)
                for t in project["tracks"]:
                    if t.get("separation_status") in ("queued", "processing"):
                        t.update(separation_status="failed", error="서버 재시작으로 분리가 중단되었습니다. 다시 시도하세요.")
                    for v in t.get("stems", []):
                        if v["status"] in ("queued", "processing"):
                            v.update(status="failed", error="서버 재시작으로 분석이 중단되었습니다. 다시 분석하세요.")
                    if t["status"] in ("queued", "processing"):
                        t.update(
                            status="failed",
                            error="서버가 재시작되어 작업이 중단되었습니다. 다시 분석하세요.",
                        )
                store.save(project)
            report_queue.recover()
        yield
        pool.shutdown(wait=True, cancel_futures=True)
        store.close()

    app = FastAPI(title="Music Analysis Studio", lifespan=lifespan)
    app.state.store = store
    app.state.jobs = jobs
    app.state.report_queue = report_queue
    app.state.auth = auth

    allowed_hosts = {"127.0.0.1", "localhost", "testserver", "::1"} | {
        h.strip().lower() for h in s.allowed_hosts.split(",") if h.strip()
    }

    @app.middleware("http")
    async def same_origin(request: Request, call_next):
        # Local service: reject foreign browser origins and DNS rebinding hosts.
        if request.url.hostname not in allowed_hosts:
            return JSONResponse(
                {"detail": translate("로컬 호스트에서만 사용할 수 있습니다.", get_language(request))}, status_code=403
            )
        # Compare host only: behind a TLS proxy the browser origin is https while uvicorn sees http.
        origin = request.headers.get("origin")
        if origin and urlsplit(origin).netloc != request.headers.get("host"):
            return JSONResponse(
                {"detail": translate("다른 출처의 요청은 허용하지 않습니다.", get_language(request))}, status_code=403
            )
        if request.method not in {"GET", "HEAD", "OPTIONS"}:
            referer = request.headers.get("referer")
            if request.headers.get("sec-fetch-site") == "cross-site" or (
                referer and urlsplit(referer).netloc != request.headers.get("host")
            ):
                return JSONResponse({"detail": translate("다른 출처의 요청은 허용하지 않습니다.", get_language(request))}, status_code=403)
        length = request.headers.get("content-length", "0")
        if length.isdigit() and int(length) > (
            s.max_upload_mb * 1024 * 1024 + 1024 * 1024
        ):
            return JSONResponse(
                {"detail": translate("요청 크기가 제한({size}MB)을 초과합니다.", get_language(request), size=s.max_upload_mb)},
                status_code=413,
            )
        try:
            denied = auth.guard(request)
        except RuntimeError as exc:
            return JSONResponse({"detail": translate(str(exc), get_language(request))}, status_code=503)
        response = denied if denied is not None else await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        # no-referrer turns Origin into null for ordinary form POSTs in Chromium.
        # Keep same-site form origins while withholding referrers from other sites.
        response.headers["Referrer-Policy"] = "same-origin"
        if not request.url.path.startswith("/static/"):
            response.headers["Cache-Control"] = "no-store"
        return response

    @app.exception_handler(RuntimeError)
    async def unavailable(request, exc):
        return JSONResponse({"detail": translate(str(exc), get_language(request))}, status_code=503)

    @app.exception_handler(StarletteHTTPException)
    async def http_error(request, exc):
        detail = translate(exc.detail, get_language(request)) if isinstance(exc.detail, str) else exc.detail
        return JSONResponse({"detail": detail}, status_code=exc.status_code, headers=exc.headers)

    def project_or_404(pid):
        project = store.get(pid)
        if project is None:
            raise HTTPException(404, "프로젝트가 없습니다.")
        return project

    def track_or_404(pid, tid):
        project = project_or_404(pid)
        track = next((t for t in project["tracks"] if t["id"] == tid), None)
        if track is None:
            raise HTTPException(404, "트랙이 없습니다.")
        return project, track

    def analysis_path(track):
        return s.storage_dir / "cache" / f"result-{track['id']}.json"

    def update_track(pid, tid, **values):
        with store.lock:
            project, track = track_or_404(pid, tid)
            track.update(values)
            project["updated_at"] = now()
            store.save(project)

    def stem_path(stem):
        return s.storage_dir / "audio" / stem["file"]

    def vocal_or_409(pid, sid, vocal_id=None):
        project, source = track_or_404(pid, sid)
        vocal = next((t for t in source.get("stems", [])
                      if t["kind"] == "vocals" and (not vocal_id or t["id"] == vocal_id)), None)
        if not vocal or not stem_path(vocal).is_file():
            raise HTTPException(409, "먼저 음악에서 보컬을 분리하세요.")
        return project, source, vocal

    def update_stem(pid, sid, vid, **values):
        with store.lock:
            project, source = track_or_404(pid, sid)
            stem = next(t for t in source["stems"] if t["id"] == vid)
            stem.update(values)
            project["updated_at"] = now()
            store.save(project)

    def new_job(pid, sid, task, vid=None):
        jid = uuid4().hex
        jobs[jid] = {"id": jid, "project_id": pid, "track_id": sid, "stem_id": vid,
                     "task": task, "status": "queued", "progress": 0, "message": "작업 대기"}
        return jid, jobs[jid]

    def submit_separation(pid, sid, model_name=None):
        jid, job = new_job(pid, sid, "separation")
        update_track(pid, sid, job_id=jid, separation_status="queued", error=None)
        def work():
            job.update(status="processing", progress=5, message="원본 음악 준비")
            target = s.storage_dir / "audio" / f"{sid}-original.wav"
            def progress(percent, message):
                job.update(progress=percent, message=message)
            try:
                _, source = track_or_404(pid, sid)
                update_track(pid, sid, separation_status="processing")
                if not target.is_file():
                    raw = s.storage_dir / "uploads" / source.get("stored_name", "_missing")
                    if not raw.is_file():
                        raw = s.storage_dir / "audio" / source.get("original_file", "_missing")
                    metadata = separation.prepare_original(raw, target, s.max_duration_seconds)
                    update_track(pid, sid, **metadata, original_file=target.name, status="ready")
                model = model_name or source.get("separation_model", s.separation_model)
                output_dir = s.storage_dir / "audio" / f"{sid}-{jid}"
                stems = separation.separate(target, output_dir, model, s.separation_device,
                                            s.storage_dir / "models", progress)
                for stem in stems:
                    stem.update(id=uuid4().hex, source_id=sid, file=f"{sid}-{jid}/{stem['kind']}.wav",
                                status="pending" if stem["kind"] == "vocals" else "ready", offset=0)
                # Keep existing stems and analysis until a replacement fully succeeds.
                with store.lock:
                    project, current = track_or_404(pid, sid)
                    previous_stems = current.get("stems", [])
                    current.update(stems=stems, separation_status="ready", separation_model=model,
                                   status="ready", error=None)
                    project["reports"] = [r for r in project.get("reports", [])
                        if not any(e.get("source_id") == sid or e["track_id"] in {t["id"] for t in source.get("stems", [])}
                                   for e in r["evidence"])]
                    project["updated_at"] = now()
                    store.save(project)
                    report_queue.forget(pid, sid)
                # Metadata now points to the new files; cleanup must not invalidate them.
                try:
                    for old in previous_stems:
                        analysis_path(old).unlink(missing_ok=True)
                        if stem_path(old).parent != s.storage_dir / "audio":
                            stem_path(old).unlink(missing_ok=True)
                            try:
                                stem_path(old).parent.rmdir()
                            except OSError:
                                pass
                    prune_cache()
                except OSError:
                    log.warning("Old separation files could not be fully cleaned up: %s", sid, exc_info=True)
                job.update(status="done", progress=100, message="보컬 · 악기 분리 완료")
            except Exception as exc:
                temporary = s.storage_dir / "audio" / f"{sid}-{jid}"
                if temporary.is_dir():
                    for path in temporary.glob("*.wav"):
                        path.unlink(missing_ok=True)
                    temporary.rmdir()
                log.exception("Source separation failed: %s", jid)
                message = str(exc) if isinstance(exc, ValueError) else "음원 분리 실패. 모델 다운로드, GPU 메모리 및 서버 로그를 확인하고 다시 시도하세요."
                _, current = track_or_404(pid, sid)
                update_track(pid, sid, separation_status="failed", status="ready" if current.get("duration") else "failed", error=message)
                job.update(status="failed", message=message)
        pool.submit(work)
        return job

    def submit_analysis(pid, sid, vid, transform_args=None):
        jid, job = new_job(pid, sid, "vocal-analysis", vid)
        update_stem(pid, sid, vid, status="queued", job_id=jid, error=None)
        def work():
            job.update(status="processing", progress=10, message="분리된 보컬 분석")
            def progress(percent, message):
                job.update(progress=percent, message=message)
            try:
                _, source = track_or_404(pid, sid)
                vocal = next(t for t in source["stems"] if t["id"] == vid)
                update_stem(pid, sid, vid, status="processing")
                if transform_args:
                    original = next(t for t in source["stems"] if t["id"] == vocal["parent_stem_id"])
                    metadata = audio.transform(stem_path(original), stem_path(vocal),
                                               **transform_args, max_duration=s.max_duration_seconds)
                    update_stem(pid, sid, vid, **metadata, waveform=separation.waveform(stem_path(vocal)))
                data, cache_hit = audio.cached_analysis(stem_path(vocal), s.storage_dir / "cache",
                                                       s.max_duration_seconds, progress)
                analysis_path(vocal).write_text(json.dumps(data, ensure_ascii=False, allow_nan=False), encoding="utf-8")
                update_stem(pid, sid, vid, status="ready", cache_hit=cache_hit, summary=data["summary"])
                job.update(status="done", progress=100, message="보컬 분석 완료")
            except Exception as exc:
                log.exception("Vocal analysis failed: %s", jid)
                message = str(exc) if isinstance(exc, ValueError) else "보컬 분석 실패. 파일과 서버 로그를 확인하세요."
                update_stem(pid, sid, vid, status="failed", error=message)
                job.update(status="failed", message=message)
        pool.submit(work)
        return job

    @app.get("/api/health")
    def health():
        return {
            "status": "degraded" if store.error else "ok",
            "database": store.mode,
            "database_error": store.error,
            "ssh_enabled": bool(s.ssh_alias),
            "ai_configured": configured(s),
            "ai_model": s.llm_ai_model,
            "audio_ai_model": s.audio_ai_model,
            "ai_connection_status": "configured_unverified" if configured(s) else "not_configured",
            "engine": audio.ENGINE,
            "device": "CPU",
            "storage": str(s.storage_dir),
            "max_upload_mb": s.max_upload_mb,
            "max_duration_seconds": s.max_duration_seconds,
            "separation_available": separation.available(),
            "separation_model": s.separation_model,
            "separation_device": s.separation_device,
        }

    @app.get("/api/projects")
    def projects(request: Request):
        return auth.visible_projects(request.state.user)

    @app.post("/api/projects", status_code=201)
    def new_project(data: ProjectInput, request: Request):
        p = {
            "id": uuid4().hex,
            "owner_id": request.state.user["user_id"],
            "name": data.name.strip() or translate("새 음악 프로젝트", get_language(request)),
            "schema_version": 2,
            "created_at": now(),
            "updated_at": now(),
            "tracks": [],
        }
        store.save(p)
        return p

    @app.get("/api/projects/{pid}")
    def project(pid: str):
        return project_or_404(pid)

    @app.patch("/api/projects/{pid}")
    def rename(pid: str, data: ProjectInput, request: Request):
        with store.lock:
            p = project_or_404(pid)
            p.update(name=data.name.strip() or translate("새 음악 프로젝트", get_language(request)), updated_at=now())
            store.save(p)
            return p

    def delete_files(source):
        parents = set()
        for stem in source.get("stems", []):
            parents.add(stem_path(stem).parent)
            stem_path(stem).unlink(missing_ok=True)
            analysis_path(stem).unlink(missing_ok=True)
        for parent in parents:
            if parent != s.storage_dir / "audio":
                try:
                    parent.rmdir()
                except OSError:
                    pass
        for filename in {source.get("original_file", "_missing"), f"{source['id']}-original.wav"}:
            (s.storage_dir / "audio" / filename).unlink(missing_ok=True)
        (s.storage_dir / "uploads" / source.get("stored_name", "_missing")).unlink(missing_ok=True)
        directory = s.storage_dir / "audio" / source["id"]
        if directory.is_dir():
            for path in directory.glob("*.wav"):
                path.unlink()
            directory.rmdir()

    def busy(p):
        return report_queue.has_pending(p["id"]) or any(t.get("separation_status") in ("queued", "processing") or
                   any(v["status"] in ("queued", "processing") for v in t.get("stems", []))
                   for t in p["tracks"])

    def prune_cache():
        used = {f"{audio.ENGINE}-{audio.pcm_digest(stem_path(v))}.json"
                for p in store.list() for t in p["tracks"] for v in t.get("stems", [])
                if v["kind"] == "vocals" and stem_path(v).is_file()}
        for path in (s.storage_dir / "cache").glob(f"{audio.ENGINE}-*.json"):
            if path.name not in used:
                path.unlink(missing_ok=True)

    @app.delete("/api/projects/{pid}", status_code=204)
    def delete_project(pid: str):
        with store.lock:
            p = project_or_404(pid)
            if busy(p):
                raise HTTPException(409, "작업 완료 후 삭제할 수 있습니다.")
            for t in p["tracks"]:
                delete_files(t)
            store.delete(pid)
            report_queue.forget(pid)
            prune_cache()

    @app.post("/api/projects/{pid}/tracks", status_code=202)
    def upload(pid: str, file: UploadFile = File(...)):
        ext = Path(file.filename or "").suffix.lower()
        if ext not in {".wav", ".mp3", ".flac"}:
            raise HTTPException(415, "WAV, MP3, FLAC 파일만 지원합니다.")
        tid = uuid4().hex
        target = s.storage_dir / "uploads" / f"{tid}{ext}"
        try:
            with target.open("wb") as out:
                size = 0
                while chunk := file.file.read(1024 * 1024):
                    size += len(chunk)
                    if size > s.max_upload_mb * 1024 * 1024:
                        raise HTTPException(
                            413,
                            f"파일당 최대 {s.max_upload_mb}MB까지 업로드할 수 있습니다.",
                        )
                    out.write(chunk)
            if size == 0:
                raise HTTPException(422, "빈 파일입니다.")
            with store.lock:
                p = project_or_404(pid)
                if len(p["tracks"]) >= 3:
                    raise HTTPException(
                        409, "한 프로젝트에는 원본 음악을 최대 3개까지 넣을 수 있습니다."
                    )
                track = {
                    "id": tid,
                    "name": Path((file.filename or "vocal").replace("\\", "/")).name[
                        :180
                    ],
                    "stored_name": target.name,
                    "sha256": audio.digest(target),
                    "status": "queued",
                    "separation_status": "queued",
                    "separation_model": s.separation_model,
                    "stems": [],
                    "duration": 0,
                    "offset": 0,
                    "size": size,
                    "created_at": now(),
                }
                p["tracks"].append(track)
                p["updated_at"] = now()
                store.save(p)
                job = submit_separation(pid, tid)
            return {"track": track, "job": job}
        except Exception:
            target.unlink(missing_ok=True)
            raise
        finally:
            file.file.close()

    @app.patch("/api/projects/{pid}/tracks/{tid}")
    def offset(pid: str, tid: str, data: TrackInput):
        update_track(pid, tid, offset=data.offset)
        return track_or_404(pid, tid)[1]

    @app.delete("/api/projects/{pid}/tracks/{tid}", status_code=204)
    def delete_track(pid: str, tid: str):
        with store.lock:
            p, t = track_or_404(pid, tid)
            if busy(p):
                raise HTTPException(409, "프로젝트의 작업 완료 후 삭제할 수 있습니다.")
            delete_files(t)
            p["tracks"] = [x for x in p["tracks"] if x["id"] != tid]
            p["reports"] = [
                r
                for r in p.get("reports", [])
                if not any(e.get("source_id") == tid or e["track_id"] in {v["id"] for v in t.get("stems", [])} for e in r["evidence"])
            ]
            p["updated_at"] = now()
            store.save(p)
            report_queue.forget(pid, tid)
            prune_cache()

    @app.post("/api/projects/{pid}/tracks/{tid}/separate", status_code=202)
    def separate_source(pid: str, tid: str, data: SeparationInput):
        with store.lock:
            p, source = track_or_404(pid, tid)
            if busy(p):
                raise HTTPException(409, "진행 중인 작업이 완료된 후 다시 시도하세요.")
            return submit_separation(pid, tid, data.model)

    @app.post("/api/projects/{pid}/tracks/{tid}/analyze", status_code=202)
    def analyze_vocal(pid: str, tid: str, data: VocalInput = VocalInput()):
        with store.lock:
            _, source, vocal = vocal_or_409(pid, tid, data.vocal_id)
            if source.get("separation_status") in ("queued", "processing") or vocal["status"] in ("queued", "processing"):
                raise HTTPException(409, "이미 처리 중입니다.")
            return submit_analysis(pid, tid, vocal["id"])

    @app.post("/api/projects/{pid}/tracks/{tid}/transform", status_code=202)
    def transform(pid: str, tid: str, data: TransformInput):
        with store.lock:
            p, source, vocal = vocal_or_409(pid, tid, data.vocal_id)
            if vocal["status"] != "ready":
                raise HTTPException(409, "분석이 완료된 보컬만 변형할 수 있습니다.")
            if len(source["stems"]) >= 12:
                raise HTTPException(409, "음원당 최대 12개 트랙까지 지원합니다.")
            vid = uuid4().hex
            variant = {"id": vid, "source_id": tid, "parent_stem_id": vocal["id"], "derived": True,
                       "kind": "vocals", "name": f"변형 보컬 ({data.semitones:+g}반음 · {data.speed:g}×)",
                       "file": f"{tid}/{vid}.wav", "status": "queued", "duration": 0, "offset": 0,
                       "transform": data.model_dump(exclude={"vocal_id"})}
            (s.storage_dir / "audio" / tid).mkdir(exist_ok=True)
            source["stems"].append(variant)
            store.save(p)
            job = submit_analysis(pid, tid, vid, variant["transform"])
            return {"track": variant, "job": job}

    @app.get("/api/projects/{pid}/tracks/{tid}/stems/{vid}/audio")
    def stem_audio(pid: str, tid: str, vid: str):
        _, source = track_or_404(pid, tid)
        stem = next((v for v in source.get("stems", []) if v["id"] == vid), None)
        if not stem or not stem_path(stem).is_file():
            raise HTTPException(404, "분리 트랙이 없습니다.")
        return FileResponse(stem_path(stem), media_type="audio/wav", filename=f"{stem['kind']}.wav")

    @app.get("/api/projects/{pid}/tracks/{tid}/stems/{vid}/analysis")
    def stem_analysis(pid: str, tid: str, vid: str):
        _, _, vocal = vocal_or_409(pid, tid, vid)
        if vocal["status"] != "ready" or not analysis_path(vocal).is_file():
            raise HTTPException(409, "보컬 분석을 먼저 실행하세요.")
        return json.loads(analysis_path(vocal).read_text(encoding="utf-8"))

    @app.get("/api/report-jobs")
    def report_jobs(request: Request):
        return report_queue.snapshot({p["id"] for p in auth.visible_projects(request.state.user)})

    @app.get("/api/jobs/{jid}")
    def job(jid: str, request: Request):
        result = jobs[jid] if jid in jobs else report_queue.get(jid)
        project = store.get(result["project_id"])
        if not project or project.get("owner_id") != request.state.user["user_id"]:
            raise HTTPException(404, "작업 정보를 찾을 수 없습니다.")
        return result

    @app.get("/api/projects/{pid}/tracks/{tid}/audio")
    def get_audio(pid: str, tid: str):
        _, track = track_or_404(pid, tid)
        path = s.storage_dir / "audio" / track.get("original_file", "_missing")
        if not path.exists() or track["status"] != "ready":
            raise HTTPException(409, "오디오 준비 중입니다.")
        return FileResponse(path, media_type="audio/wav", filename=f"{tid}.wav")

    @app.get("/api/projects/{pid}/tracks/{tid}/original")
    def original(pid: str, tid: str):
        _, track = track_or_404(pid, tid)
        path = s.storage_dir / "uploads" / track.get("stored_name", "_missing")
        if not path.is_file():
            raise HTTPException(404, "변형본에는 원본 업로드 파일이 없습니다.")
        return FileResponse(path, filename=track["name"])

    @app.get("/api/projects/{pid}/tracks/{tid}/analysis")
    def get_analysis(pid: str, tid: str):
        _, _, vocal = vocal_or_409(pid, tid)
        if vocal["status"] != "ready" or not analysis_path(vocal).is_file():
            raise HTTPException(409, "보컬 분석을 먼저 실행하세요.")
        return json.loads(analysis_path(vocal).read_text(encoding="utf-8"))

    @app.get("/api/projects/{pid}/reports/{index}")
    def saved_report(pid: str, index: int):
        reports = project_or_404(pid).get("reports", [])
        if index < 0 or index >= len(reports):
            raise HTTPException(404, "리포트가 없습니다.")
        report = dict(reports[index])
        report["content_html"] = render_markdown(report["content"])
        return report

    @app.post("/api/projects/{pid}/reports", status_code=202)
    def report(pid: str, data: ReportInput, request: Request):
        with store.lock:
            p = project_or_404(pid)
            if data.end <= data.start:
                raise HTTPException(422, "구간 끝은 시작보다 커야 합니다.")
            vocals = []
            for source in p["tracks"]:
                if data.source_ids is not None and source["id"] not in data.source_ids:
                    continue
                if source.get("separation_status") in ("queued", "processing"):
                    raise HTTPException(409, "선택 음원의 분리 작업 완료 후 리포트를 요청하세요.")
                for vocal in source.get("stems", []):
                    if vocal["kind"] != "vocals" or vocal["status"] != "ready":
                        continue
                    if data.vocal_id and vocal["id"] != data.vocal_id:
                        continue
                    if not data.vocal_id and vocal.get("derived"):
                        continue
                    vocals.append({**vocal, "name": source["name"] + " · " + translate(vocal["name"], get_language(request)),
                                   "source_id": source["id"], "offset": 0 if data.vocal_id else source.get("offset", 0)})
            analyses = {v["id"]: json.loads(analysis_path(v).read_text(encoding="utf-8"))
                        for v in vocals if analysis_path(v).is_file()}
            items = evidence(vocals, analyses, data.start, data.end)
            if not items:
                raise HTTPException(409, "선택 구간에 분석 데이터가 없습니다.")
            if data.source_ids and len(data.source_ids) > 1 and len(items) < 2:
                raise HTTPException(409, "비교하려면 최소 두 음원의 보컬 분석을 완료하세요.")
            if data.ai and not configured(s):
                raise HTTPException(
                    409,
                    "AI 미설정: .env에서 AUDIO_AI_URL/MODEL/API_KEY와 LLM_AI_URL/MODEL/API_KEY를 설정하세요.",
                )
            return report_queue.submit(p, items, data.start, data.end, data.ai, data.enqueue,
                                       language=get_language(request))

    register_auth(app, auth)
    register_language(app, s)
    register_ui(app, store, health, report_queue.snapshot)

    return app


app = create_app()
