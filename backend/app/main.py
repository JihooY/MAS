import json
import logging
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import audio
from .config import ROOT, Settings
from .database import Store
from .reports import ai_report, evidence, measured_report

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


class ReportInput(BaseModel):
    start: float = Field(default=0, ge=0)
    end: float = Field(gt=0)
    ai: bool = False


def create_app(settings=None):
    s = settings or Settings()
    s.prepare()
    store = Store(s)
    jobs = {}
    pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="vocal-worker")

    @asynccontextmanager
    async def lifespan(app):
        store.connect()
        if not store.error:
            for project in store.list():
                for t in project["tracks"]:
                    if t["status"] in ("queued", "processing"):
                        t.update(status="failed", error="서버가 재시작되어 작업이 중단되었습니다. 다시 분석하세요.")
                store.save(project)
        yield
        pool.shutdown(wait=True, cancel_futures=True)
        store.close()

    app = FastAPI(title="Music Analysis Studio", lifespan=lifespan)
    app.state.store = store
    app.state.jobs = jobs

    @app.middleware("http")
    async def same_origin(request: Request, call_next):
        # Local service: reject foreign browser origins and DNS rebinding hosts.
        if request.url.hostname not in {"127.0.0.1", "localhost", "testserver", "::1"}:
            return JSONResponse({"detail": "로컬 호스트에서만 사용할 수 있습니다."}, status_code=403)
        origin = request.headers.get("origin")
        if origin and origin != f"{request.url.scheme}://{request.headers.get('host')}":
            return JSONResponse({"detail": "다른 출처의 요청은 허용하지 않습니다."}, status_code=403)
        length = request.headers.get("content-length", "0")
        if length.isdigit() and int(length) > (s.max_upload_mb * 1024 * 1024 + 1024 * 1024):
            return JSONResponse({"detail": f"요청 크기가 제한({s.max_upload_mb}MB)을 초과합니다."}, status_code=413)
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        return response

    @app.exception_handler(RuntimeError)
    async def unavailable(request, exc):
        return JSONResponse({"detail": str(exc)}, status_code=503)

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

    def submit(pid, tid, transform_args=None, source_id=None):
        jid = uuid4().hex
        jobs[jid] = {"id": jid, "project_id": pid, "track_id": tid, "status": "queued", "progress": 0, "message": "분석 대기"}
        update_track(pid, tid, job_id=jid, status="queued", error=None)

        def work():
            job = jobs[jid]
            def progress(value, message):
                job.update(progress=value, message=message)
            job.update(status="processing", progress=5, message="오디오 준비")
            target = s.storage_dir / "audio" / f"{tid}.wav"
            try:
                _, track = track_or_404(pid, tid)
                update_track(pid, tid, status="processing")
                if transform_args:
                    metadata = audio.transform(s.storage_dir / "audio" / f"{source_id}.wav", target,
                                               **transform_args, max_duration=s.max_duration_seconds)
                elif not target.exists():
                    metadata = audio.decode(s.storage_dir / "uploads" / track["stored_name"], target, s.max_duration_seconds)
                else:
                    metadata = {}
                update_track(pid, tid, **metadata)
                data, cache_hit = audio.cached_analysis(target, s.storage_dir / "cache", s.max_duration_seconds, progress)
                analysis_path(track).write_text(json.dumps(data, ensure_ascii=False, allow_nan=False), encoding="utf-8")
                update_track(pid, tid, status="ready", cache_hit=cache_hit, summary=data["summary"])
                job.update(status="done", progress=100, message="분석 완료")
            except Exception as exc:
                log.exception("Audio job %s failed", jid)
                message = str(exc) if isinstance(exc, ValueError) else "처리 실패. 파일 형식·저장 공간을 확인하고 서버 로그를 참고하세요."
                try:
                    update_track(pid, tid, status="failed", error=message)
                except Exception:
                    log.exception("Could not save failed job")
                job.update(status="failed", message=message)
        pool.submit(work)
        return jobs[jid]

    @app.get("/api/health")
    def health():
        return {"status": "degraded" if store.error else "ok", "database": store.mode,
                "database_error": store.error, "ssh_enabled": bool(s.ssh_alias),
                "ai_configured": bool(s.ai_url and s.ai_model), "ai_model": s.ai_model,
                "engine": audio.ENGINE, "device": "CPU", "storage": str(s.storage_dir),
                "max_upload_mb": s.max_upload_mb, "max_duration_seconds": s.max_duration_seconds}

    @app.get("/api/projects")
    def projects():
        return store.list()

    @app.post("/api/projects", status_code=201)
    def new_project(data: ProjectInput):
        p = {"id": uuid4().hex, "name": data.name.strip() or "새 보컬 프로젝트", "created_at": now(), "updated_at": now(), "tracks": []}
        store.save(p)
        return p

    @app.get("/api/projects/{pid}")
    def project(pid: str):
        return project_or_404(pid)

    @app.patch("/api/projects/{pid}")
    def rename(pid: str, data: ProjectInput):
        with store.lock:
            p = project_or_404(pid)
            p.update(name=data.name.strip() or "새 보컬 프로젝트", updated_at=now())
            store.save(p)
            return p

    def delete_files(track):
        for path in [s.storage_dir / "audio" / f"{track['id']}.wav", analysis_path(track),
                     s.storage_dir / "uploads" / track.get("stored_name", "_missing")]:
            path.unlink(missing_ok=True)

    def busy(p):
        return any(t["status"] in ("queued", "processing") for t in p["tracks"])

    def prune_cache():
        used = set()
        for p in store.list():
            for t in p["tracks"]:
                path = s.storage_dir / "audio" / f"{t['id']}.wav"
                if path.exists():
                    used.add(f"{audio.ENGINE}-{audio.pcm_digest(path)}.json")
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
                        raise HTTPException(413, f"파일당 최대 {s.max_upload_mb}MB까지 업로드할 수 있습니다.")
                    out.write(chunk)
            if size == 0:
                raise HTTPException(422, "빈 파일입니다.")
            with store.lock:
                p = project_or_404(pid)
                if len(p["tracks"]) >= 3:
                    raise HTTPException(409, "한 프로젝트에서 최대 3개 트랙을 비교할 수 있습니다.")
                track = {"id": tid, "name": Path((file.filename or "vocal").replace("\\", "/")).name[:180],
                         "stored_name": target.name, "sha256": audio.digest(target), "status": "queued",
                         "duration": 0, "offset": 0, "size": size, "created_at": now()}
                p["tracks"].append(track)
                p["updated_at"] = now()
                store.save(p)
                job = submit(pid, tid)
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
            p["reports"] = [r for r in p.get("reports", [])
                            if not any(e["track_id"] == tid for e in r["evidence"])]
            p["updated_at"] = now()
            store.save(p)
            prune_cache()

    @app.post("/api/projects/{pid}/tracks/{tid}/analyze", status_code=202)
    def retry(pid: str, tid: str):
        with store.lock:
            _, track = track_or_404(pid, tid)
            if track["status"] in ("queued", "processing"):
                raise HTTPException(409, "이미 처리 중입니다.")
            params = track.get("transform") if not (s.storage_dir / "audio" / f"{tid}.wav").exists() else None
            return submit(pid, tid, params, track.get("source_id"))

    @app.post("/api/projects/{pid}/tracks/{tid}/transform", status_code=202)
    def transform(pid: str, tid: str, data: TransformInput):
        with store.lock:
            p, source = track_or_404(pid, tid)
            if source["status"] != "ready":
                raise HTTPException(409, "분석이 완료된 트랙만 변형할 수 있습니다.")
            if len(p["tracks"]) >= 3:
                raise HTTPException(409, "변형본을 추가하려면 트랙 슬롯 하나를 비워주세요.")
            new_id = uuid4().hex
            track = {"id": new_id, "name": f"변형 · {source['name']}"[:180], "source_id": tid,
                     "transform": data.model_dump(), "status": "queued", "duration": 0,
                     "offset": source.get("offset", 0), "created_at": now(), "size": 0}
            p["tracks"].append(track)
            p["updated_at"] = now()
            store.save(p)
            return {"track": track, "job": submit(pid, new_id, data.model_dump(), tid)}

    @app.get("/api/jobs/{jid}")
    def job(jid: str):
        if jid not in jobs:
            raise HTTPException(404, "작업 정보를 찾을 수 없습니다.")
        return jobs[jid]

    @app.get("/api/projects/{pid}/tracks/{tid}/audio")
    def get_audio(pid: str, tid: str):
        _, track = track_or_404(pid, tid)
        path = s.storage_dir / "audio" / f"{track['id']}.wav"
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
        _, track = track_or_404(pid, tid)
        if track["status"] != "ready" or not analysis_path(track).exists():
            raise HTTPException(409, "분석이 아직 완료되지 않았습니다.")
        return json.loads(analysis_path(track).read_text(encoding="utf-8"))

    @app.post("/api/projects/{pid}/reports", status_code=202)
    def report(pid: str, data: ReportInput):
        p = project_or_404(pid)
        if data.end <= data.start:
            raise HTTPException(422, "구간 끝은 시작보다 커야 합니다.")
        analyses = {t["id"]: json.loads(analysis_path(t).read_text(encoding="utf-8"))
                    for t in p["tracks"] if t["status"] == "ready"}
        items = evidence(p["tracks"], analyses, data.start, data.end)
        if not items:
            raise HTTPException(409, "선택 구간에 분석 데이터가 없습니다.")
        if data.ai and not (s.ai_url and s.ai_model):
            raise HTTPException(409, "AI 미설정: .env에서 MAS_AI_URL, MAS_AI_MODEL 및 필요 시 API 키를 설정하세요.")
        jid = uuid4().hex
        jobs[jid] = {"id": jid, "status": "queued", "progress": 0, "message": "리포트 생성 대기"}
        def work():
            jobs[jid].update(status="processing", progress=25, message="AI 평가 중" if data.ai else "측정 리포트 작성 중")
            try:
                content = ai_report(s, items) if data.ai else measured_report(items, data.start, data.end)
                result = {"content": content, "evidence": items,
                    "kind": "ai" if data.ai else "measured", "model": s.ai_model if data.ai else None, "created_at": now()}
                with store.lock:
                    current = store.get(pid)
                    if current is not None:
                        current["reports"] = (current.get("reports", []) + [result])[-10:]
                        store.save(current)
                jobs[jid].update(status="done", progress=100, result=result)
            except Exception:
                log.exception("Report failed")
                jobs[jid].update(status="failed", message="AI 제공자 연결·인증·응답 오류입니다. .env의 URL, 모델, API 키를 확인하세요.")
        pool.submit(work)
        return jobs[jid]

    dist = ROOT / "frontend" / "dist"
    if dist.exists():
        app.mount("/", StaticFiles(directory=dist, html=True), name="frontend")
    else:
        @app.get("/")
        def setup():
            return {"message": "frontend에서 npm install 및 npm run build 후 서버를 다시 시작하세요.", "docs": "/docs"}
    return app


app = create_app()
