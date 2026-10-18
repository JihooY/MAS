"""Run the complete Jinja UI and API with `uv run python run.py`."""

import uvicorn

from backend.app.config import Settings

if __name__ == "__main__":
    s = Settings()
    uvicorn.run("backend.app.main:app", host=s.host, port=s.port, workers=1)
