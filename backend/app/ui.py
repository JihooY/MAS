"""Server-rendered pages and small HTML fragments; no separate frontend runtime."""
from pathlib import Path

from fastapi import HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

UI_ROOT = Path(__file__).parent
COLORS = ["#60a5fa", "#c084fc", "#34d399"]
NAV = [
    {"id": "dashboard", "label": "대시보드", "icon": "layout-dashboard"},
    {"id": "workspace", "label": "워크스페이스", "icon": "audio-lines"},
    {"id": "compare", "label": "보컬 비교", "icon": "arrow-left-right"},
    {"id": "analysis", "label": "상세 분석", "icon": "bar-chart3"},
    {"id": "reports", "label": "AI 평가", "icon": "sparkles"},
]


def format_time(value):
    seconds = max(0, float(value or 0))
    return f"{int(seconds // 60):02d}:{int(seconds % 60):02d}"


def register_ui(app, store, get_health):
    templates = Jinja2Templates(directory=UI_ROOT / "templates")
    templates.env.filters["clock"] = format_time
    templates.env.globals.update(colors=COLORS, nav=NAV)
    app.mount("/static", StaticFiles(directory=UI_ROOT / "static"), name="static")

    def context(request, page, project_id=None):
        error = None
        try:
            projects = store.list()
        except Exception:
            projects = []
            error = get_health().get("database_error") or "저장소 연결 실패. MongoDB와 SSH 설정을 확인하세요."
        project = next((p for p in projects if p["id"] == project_id), None)
        if project_id and not project and not error:
            raise HTTPException(404, "프로젝트가 없습니다.")
        if project is None and not project_id and projects:
            project = projects[0]
        tracks = project["tracks"] if project else []
        ready = [t for t in tracks if t["status"] == "ready"]
        duration = max([max(0, t["duration"] - t.get("offset", 0)) for t in ready], default=0)
        all_tracks = [t for p in projects for t in p["tracks"]]
        health = get_health()
        return {
            "request": request, "page": page,
            "page_label": next(n["label"] for n in NAV if n["id"] == page),
            "projects": projects, "project": project, "tracks": tracks, "ready": ready,
            "busy": any(t["status"] in ("queued", "processing") for t in tracks),
            "duration": duration, "summary": tracks[0].get("summary") if tracks else None,
            "overview": [len(projects), len(all_tracks), sum(t["status"] == "ready" for t in all_tracks),
                         sum(t["status"] in ("queued", "processing") for t in all_tracks)],
            "health": health, "error": error,
            "bootstrap": {"page": page, "project": project, "health": health},
        }

    @app.get("/", response_class=HTMLResponse, include_in_schema=False)
    def home(request: Request, project: str | None = None):
        return templates.TemplateResponse(request=request, name="workspace.html", context=context(request, "workspace", project))

    # Every screen is a real Jinja response with populated markup, even without JS.
    def page_handler(page):
        def render(request: Request, project: str | None = None):
            data = context(request, page, project)
            return templates.TemplateResponse(request=request, name="dashboard.html" if page == "dashboard" else "workspace.html", context=data)
        return render

    for item in NAV:
        app.add_api_route(f"/{item['id']}", page_handler(item["id"]), methods=["GET"],
                          response_class=HTMLResponse, include_in_schema=False, name=item["id"])

    @app.get("/ui/tracks/{project_id}", response_class=HTMLResponse, include_in_schema=False)
    def track_fragment(request: Request, project_id: str):
        return templates.TemplateResponse(request=request, name="partials/tracks.html", context=context(request, "workspace", project_id))
