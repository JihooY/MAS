"""Server-rendered pages and small HTML fragments; no separate frontend runtime."""
from pathlib import Path

from fastapi import HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from .markdown import render_markdown
from .project import upgrade_project
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

UI_ROOT = Path(__file__).parent
COLORS = ["#60a5fa", "#c084fc", "#34d399"]
NAV = [
    {"id": "dashboard", "label": "프로젝트", "icon": "layout-dashboard"},
    {"id": "workspace", "label": "스튜디오", "icon": "audio-lines"},
]


def format_time(value):
    seconds = max(0, float(value or 0))
    return f"{int(seconds // 60):02d}:{int(seconds % 60):02d}"


def register_ui(app, store, get_health, get_report_queue):
    templates = Jinja2Templates(directory=UI_ROOT / "templates")
    templates.env.filters["clock"] = format_time
    templates.env.globals.update(colors=COLORS, nav=NAV)
    app.mount("/static", StaticFiles(directory=UI_ROOT / "static"), name="static")

    def context(request, page, project_id=None, source_id=None):
        error = None
        try:
            projects = [upgrade_project(p) for p in store.list()]
        except Exception:
            projects = []
            error = get_health().get("database_error") or "저장소 연결 실패. MongoDB와 SSH 설정을 확인하세요."
        project = next((p for p in projects if p["id"] == project_id), None)
        if project_id and not project and not error:
            raise HTTPException(404, "프로젝트가 없습니다.")
        if project is None and not project_id and projects:
            project = projects[0]
        sources = project["tracks"] if project else []
        source = next((t for t in sources if t["id"] == source_id), None)
        if source_id and source is None:
            raise HTTPException(404, "프로젝트에 해당 음원이 없습니다.")
        source = source or (sources[0] if sources else None)
        stems = source.get("stems", []) if source else []
        # The original is available for reference; stems form the default mix.
        playback = []
        if source and source["status"] == "ready":
            playback.append({**source, "id": source["id"] + "-original", "name": "원본 음악",
                             "kind": "original", "status": "ready", "source_id": source["id"],
                             "url": f"/api/projects/{project['id']}/tracks/{source['id']}/audio"})
        for v in sorted(stems, key=lambda t: (t["kind"] != "vocals", t.get("derived", False))):
            if v.get("duration", 0):
                playback.append({**v, "status": "ready", "offset": 0,
                    "url": f"/api/projects/{project['id']}/tracks/{source['id']}/stems/{v['id']}/audio"})
        vocal = next((v for v in stems if v["kind"] == "vocals" and not v.get("derived")), None)
        ready = [v for v in stems if v["kind"] == "vocals" and v["status"] == "ready"]
        all_sources = [t for p in projects for t in p["tracks"]]
        health = get_health()
        queue = get_report_queue() if not error else {"busy": False, "jobs": [], "recent": []}
        if project:
            for report in project.get("reports", []):
                report["content_html"] = render_markdown(report["content"])
        return {
            "request": request, "page": page, "page_label": "프로젝트" if page == "dashboard" else "스튜디오",
            "projects": projects, "project": project, "sources": sources, "source": source,
            "tracks": playback, "vocal": vocal, "vocals": [v for v in stems if v["kind"] == "vocals"],
            "ready": ready, "busy": any(t.get("separation_status") in ("queued", "processing") or
                any(v["status"] in ("queued", "processing") for v in t.get("stems", [])) for t in sources),
            "duration": source.get("duration", 0) if source else 0,
            "summary": vocal.get("summary") if vocal else None,
            "overview": [len(projects), len(all_sources), sum(t.get("separation_status") == "ready" for t in all_sources),
                         sum(t.get("separation_status") in ("queued", "processing") for t in all_sources)],
            "health": health, "error": error,
            "report_queue": queue,
            "bootstrap": {"page": page, "project": project, "source_id": source["id"] if source else None,
                          "health": health, "report_queue": queue},
        }

    @app.get("/", response_class=HTMLResponse, include_in_schema=False)
    def home(request: Request, project: str | None = None, source: str | None = None):
        return templates.TemplateResponse(request=request, name="workspace.html", context=context(request, "workspace", project, source))

    # Every screen is a real Jinja response with populated markup, even without JS.
    def page_handler(page):
        def render(request: Request, project: str | None = None, source: str | None = None):
            data = context(request, page, project, source)
            return templates.TemplateResponse(request=request, name="dashboard.html" if page == "dashboard" else "workspace.html", context=data)
        return render

    for item in NAV:
        app.add_api_route(f"/{item['id']}", page_handler(item["id"]), methods=["GET"],
                          response_class=HTMLResponse, include_in_schema=False, name=item["id"])

    @app.get("/ui/tracks/{project_id}", response_class=HTMLResponse, include_in_schema=False)
    def track_fragment(request: Request, project_id: str, source: str | None = None):
        return templates.TemplateResponse(request=request, name="partials/tracks.html", context=context(request, "workspace", project_id, source))

    @app.get("/compare", include_in_schema=False)
    @app.get("/analysis", include_in_schema=False)
    @app.get("/reports", include_in_schema=False)
    def old_screen(request: Request):
        # Preserve bookmarks; analysis now belongs inside the project workspace.
        suffix = f"?{request.url.query}" if request.url.query else ""
        return RedirectResponse("/workspace" + suffix, status_code=307)
