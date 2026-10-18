import io
import time

import mongomock
import numpy as np
import pytest
import soundfile as sf
from fastapi.testclient import TestClient

from backend.app.config import Settings
from backend.app.database import Store
from backend.app.main import create_app


def wav():
    t = np.arange(22050) / 22050
    f = io.BytesIO()
    sf.write(f, 0.2 * np.sin(2 * np.pi * 220 * t), 22050, format="WAV")
    return f.getvalue()


def wait_job(client, jid):
    # First pYIN invocation compiles numba kernels; later runs reuse its cache.
    deadline = time.monotonic() + 180
    while time.monotonic() < deadline:
        result = client.get(f"/api/jobs/{jid}").json()
        if result["status"] in {"done", "failed"}:
            assert result["status"] == "done", result
            return result
        time.sleep(0.05)
    pytest.fail("background job timeout")


@pytest.fixture
def client(tmp_path):
    settings = Settings(
        _env_file=None,
        storage_dir=tmp_path,
        mongo_uri="",
        ssh_alias="",
        ai_url="",
        ai_model="",
    )
    with TestClient(create_app(settings)) as c:
        yield c


def test_upload_analysis_compare_transform_report_delete(client):
    assert client.get("/api/health").json()["database"] == "local-preview"
    p = client.post("/api/projects", json={"name": "보컬 테스트"}).json()
    base = f"/api/projects/{p['id']}"
    raw = wav()
    upload = client.post(
        base + "/tracks", files={"file": ("../../tone.wav", raw, "audio/wav")}
    )
    assert upload.status_code == 202
    t = upload.json()["track"]
    assert t["name"] == "tone.wav"
    wait_job(client, upload.json()["job"]["id"])
    result = client.get(base + f"/tracks/{t['id']}/analysis").json()
    assert result["summary"]["pitch_median_hz"] == pytest.approx(220, abs=3)
    resp = client.get(
        base + f"/tracks/{t['id']}/audio", headers={"Range": "bytes=0-99"}
    )
    assert resp.status_code == 206 and len(resp.content) == 100
    assert client.get(base + f"/tracks/{t['id']}/original").content == raw
    again = client.post(
        base + "/tracks", files={"file": ("again.wav", raw, "audio/wav")}
    ).json()
    wait_job(client, again["job"]["id"])
    assert client.get(base).json()["tracks"][1]["cache_hit"]
    changed = client.post(
        base + f"/tracks/{t['id']}/transform",
        json={"semitones": 12, "speed": 1, "normalize": True},
    )
    assert changed.status_code == 202
    wait_job(client, changed.json()["job"]["id"])
    assert (
        client.post(base + "/tracks", files={"file": ("fourth.wav", raw)}).status_code
        == 409
    )
    assert (
        client.patch(base + f"/tracks/{t['id']}", json={"offset": 0.2}).status_code
        == 200
    )
    report = client.post(base + "/reports", json={"start": 0.1, "end": 0.8}).json()
    content = wait_job(client, report["id"])["result"]
    assert content["kind"] == "measured" and len(content["evidence"]) == 3
    assert "평가 해석" in content["content"]
    assert len(client.get(base).json()["reports"]) == 1
    assert (
        client.post(
            base + "/reports", json={"start": 0, "end": 0.5, "ai": True}
        ).status_code
        == 409
    )
    assert (
        client.post(base + "/reports", json={"start": 2, "end": 1}).status_code == 422
    )
    assert client.delete(base).status_code == 204
    assert client.get(base).status_code == 404
    assert list(client.app.state.store.settings.storage_dir.rglob("*.wav")) == []
    assert (
        list((client.app.state.store.settings.storage_dir / "cache").glob("*.json"))
        == []
    )


def test_reject_invalid_files_and_foreign_origin(client):
    pid = client.post("/api/projects", json={"name": "test"}).json()["id"]
    base = f"/api/projects/{pid}/tracks"
    assert client.post(base, files={"file": ("malware.exe", b"xxx")}).status_code == 415
    assert client.post(base, files={"file": ("empty.wav", b"")}).status_code == 422
    bad = client.post(base, files={"file": ("bad.wav", b"not wave")}).json()
    for _ in range(500):
        job = client.get(f"/api/jobs/{bad['job']['id']}").json()
        if job["status"] == "failed":
            break
        time.sleep(0.02)
    assert job["status"] == "failed"
    assert (
        client.post(
            "/api/projects",
            json={"name": "x"},
            headers={"Origin": "https://evil.example"},
        ).status_code
        == 403
    )
    assert (
        client.get("/api/health", headers={"Host": "evil.example"}).status_code == 403
    )
    assert client.get("/api/projects/../../etc").status_code == 404


def test_mongo_store_roundtrip(tmp_path):
    s = Settings(_env_file=None, storage_dir=tmp_path, mongo_uri="mongodb://unused")
    s.prepare()
    store = Store(s)
    store.collection = mongomock.MongoClient().db.projects
    project = {"id": "123", "name": "보컬", "updated_at": "2026", "tracks": []}
    store.save(project)
    assert "_id" not in project
    assert store.get("123") == project
    store.delete("123")
    assert store.list() == []


def test_jinja_pages_are_server_rendered_and_escape_names(client):
    name = '<script>alert("project")</script>'
    project = client.post('/api/projects', json={'name': name}).json()
    for path in ['/', '/dashboard', '/workspace', '/compare', '/analysis', '/reports']:
        page = client.get(path, params={'project': project['id']})
        assert page.status_code == 200, page.text
        assert page.headers['content-type'].startswith('text/html')
        assert '&lt;script&gt;' in page.text
        assert name not in page.text
        assert '/static/app.js' in page.text
        assert '/static/app.css' in page.text
        assert 'studio-data' in page.text
    assert client.get('/workspace?project=missing').status_code == 404


def test_jinja_track_fragment_and_local_assets(client):
    project = client.post('/api/projects', json={'name': '서버 렌더링'}).json()
    stored = client.app.state.store.get(project['id'])
    stored['tracks'] = [{'id': 'safe-id', 'name': '<img src=x onerror=alert(1)>.wav',
                         'status': 'queued', 'duration': 0, 'offset': 0}]
    client.app.state.store.save(stored)
    fragment = client.get(f"/ui/tracks/{project['id']}")
    assert fragment.status_code == 200
    assert 'data-track="safe-id"' in fragment.text
    assert '&lt;img' in fragment.text and '<img src=x' not in fragment.text
    assert 'disabled' in fragment.text and '분석 대기 중' in fragment.text
    for path in ['app.js', 'api.js', 'player.js', 'charts.js', 'app.css', 'icons.svg',
                 'reference/theme.css', 'reference/fonts.css',
                 'reference/fonts/inter-latin-wght-normal.woff2']:
        response = client.get(f'/static/{path}')
        assert response.status_code == 200, path
        assert len(response.content) > 0
