"""Run with `uv run python run.py` after building the frontend."""
from pathlib import Path
import uvicorn

if __name__ == "__main__":
    if not (Path(__file__).parent / "frontend/dist/index.html").exists():
        raise SystemExit("프론트엔드를 먼저 빌드하세요: node tools/build_frontend.mjs")
    uvicorn.run("backend.app.main:app", host="127.0.0.1", port=8000, workers=1)
