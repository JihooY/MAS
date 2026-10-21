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
from backend.app import separation


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


def test_manual_structure_sections_validate_persist_and_delete(client):
    project = client.post('/api/projects', json={'name': 'structure'}).json()
    base = f"/api/projects/{project['id']}"
    upload = client.post(base+'/tracks', files={'file': ('song.wav', wav(), 'audio/wav')}).json()
    wait_job(client, upload['job']['id'])
    route = base+'/tracks/'+upload['track']['id']+'/sections'
    for value in ({'name': ' ', 'start': 0, 'end': .5}, {'name': '후렴', 'start': 0, 'end': 2}, {'name': '후렴', 'start': .5, 'end': .51}):
        assert client.post(route, json=value).status_code == 422
    section = client.post(route, json={'name': '후렴 1', 'start': .2, 'end': .7})
    assert section.status_code == 201 and section.json()['origin'] == 'manual'
    stored = client.get(base).json()['tracks'][0]['sections'][0]
    assert stored['name'] == '후렴 1'
    assert client.delete(route+'/'+stored['id']).status_code == 204
    assert client.get(base).json()['tracks'][0]['sections'] == []


@pytest.fixture
def client(tmp_path, monkeypatch):
    # API orchestration fixture only. Real model inference is verified separately.
    def fixture_separator(source, output_dir, model_name, device, model_dir, progress):
        output_dir.mkdir(parents=True, exist_ok=True)
        y, sr = sf.read(source, dtype="float32", always_2d=True)
        stems = []
        for name in separation.MODELS[model_name]:
            path = output_dir / f"{name}.wav"
            sf.write(path, y / 4 if name != "vocals" else y, sr, subtype="FLOAT")
            stems.append({"kind": name, "name": separation.LABELS[name], "duration": len(y)/sr,
                          "sample_rate": sr, "channels": 2, "waveform": separation.waveform(path)})
        return stems
    monkeypatch.setattr(separation, "separate", fixture_separator)
    settings = Settings(
        _env_file=None,
        storage_dir=tmp_path,
        mongo_uri="",
        ssh_alias="",
        audio_ai_url="",
        llm_ai_url="",
    )
    with TestClient(create_app(settings)) as c:
        assert c.post('/register', data={'email': 'tester@gmail.com',
            'password': 'test-password-123', 'password_confirm': 'test-password-123'},
            follow_redirects=False).status_code == 303
        yield c


def test_original_separation_vocal_analysis_variants_comparison_and_delete(client):
    project = client.post("/api/projects", json={"name": "원본 음악 프로젝트"}).json()
    base = f"/api/projects/{project['id']}"
    raw = wav()
    upload = client.post(base + "/tracks", files={"file": ("../../song.wav", raw, "audio/wav")})
    assert upload.status_code == 202
    source = upload.json()["track"]
    assert source["name"] == "song.wav"
    wait_job(client, upload.json()["job"]["id"])
    source = client.get(base).json()["tracks"][0]
    assert source["separation_status"] == "ready"
    assert {v["kind"] for v in source["stems"]} == {"vocals", "drums", "bass", "other"}
    vocal = next(v for v in source["stems"] if v["kind"] == "vocals")
    assert vocal["status"] == "pending"  # Original music is never analyzed as a vocal.
    assert client.get(base + f"/tracks/{source['id']}/analysis").status_code == 409
    assert client.get(base + f"/tracks/{source['id']}/original").content == raw
    response = client.get(base + f"/tracks/{source['id']}/stems/{vocal['id']}/audio", headers={"Range": "bytes=0-99"})
    assert response.status_code == 206 and len(response.content) == 100
    analyze = client.post(base + f"/tracks/{source['id']}/analyze", json={}).json()
    wait_job(client, analyze["id"])
    result = client.get(base + f"/tracks/{source['id']}/stems/{vocal['id']}/analysis").json()
    assert result["summary"]["pitch_median_hz"] == pytest.approx(220, abs=3)
    second = client.post(base + "/tracks", files={"file": ("second.wav", raw)}).json()
    wait_job(client, second["job"]["id"])
    wait_job(client, client.post(base + f"/tracks/{second['track']['id']}/analyze", json={}).json()["id"])
    source2 = client.get(base).json()["tracks"][1]
    assert next(v for v in source2["stems"] if v["kind"] == "vocals")["cache_hit"]
    changed = client.post(base + f"/tracks/{source['id']}/transform", json={"semitones":12,"speed":1,"normalize":True})
    assert changed.status_code == 202
    wait_job(client, changed.json()["job"]["id"])
    assert len(client.get(base).json()["tracks"]) == 2
    assert len(client.get(base).json()["tracks"][0]["stems"]) == 5
    third = client.post(base + "/tracks", files={"file": ("third.wav", raw)}).json()
    wait_job(client, third["job"]["id"])
    assert client.post(base + "/tracks", files={"file": ("fourth.wav", raw)}).status_code == 409
    report = client.post(base + "/reports", json={"start":.1,"end":.8,"source_ids":[source['id'],source2['id']]}).json()
    result = wait_job(client, report["id"])["result"]
    assert len(result["evidence"]) == 2 and result["kind"] == "measured"
    assert "<h2>" in result["content_html"] and "<strong>" in result["content_html"] and "<table>" in result["content_html"]
    assert client.get(base + "/reports/0").json()["content_html"] == result["content_html"]
    assert client.post(base + "/reports", json={"start":0,"end":.5,"ai":True}).status_code == 409
    assert client.post(base + "/reports", json={"start":2,"end":1}).status_code == 422
    assert client.delete(base).status_code == 204
    assert list(client.app.state.store.settings.storage_dir.rglob("*.wav")) == []
    assert list((client.app.state.store.settings.storage_dir / "cache").glob("*.json")) == []


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


def test_failed_reseparation_preserves_existing_stems_and_can_retry(client, monkeypatch):
    pid = client.post('/api/projects', json={'name': 'retry'}).json()['id']
    base = f'/api/projects/{pid}'
    upload = client.post(base + '/tracks', files={'file': ('source.wav', wav())}).json()
    wait_job(client, upload['job']['id'])
    original = client.get(base).json()['tracks'][0]
    path = base + f"/tracks/{original['id']}"
    fixture_separator = separation.separate

    def fail(*args, **kwargs):
        raise ValueError('테스트 분리 오류')

    monkeypatch.setattr(separation, 'separate', fail)
    jid = client.post(path + '/separate', json={'model': 'htdemucs_6s'}).json()['id']
    for _ in range(500):
        job = client.get(f'/api/jobs/{jid}').json()
        if job['status'] == 'failed':
            break
        time.sleep(.02)
    assert job['status'] == 'failed'
    failed = client.get(base).json()['tracks'][0]
    assert failed['stems'] == original['stems']
    assert failed['separation_status'] == 'failed' and failed['status'] == 'ready'
    assert client.get(path + '/audio').status_code == 200
    for stem in failed['stems']:
        assert client.get(path + f"/stems/{stem['id']}/audio").status_code == 200

    monkeypatch.setattr(separation, 'separate', fixture_separator)
    wait_job(client, client.post(path + '/separate', json={'model': 'htdemucs_6s'}).json()['id'])
    ready = client.get(base).json()['tracks'][0]
    assert ready['separation_status'] == 'ready'
    assert len(ready['stems']) == 6
    assert client.get(path + f"/stems/{original['stems'][0]['id']}/audio").status_code == 404


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


def test_jinja_source_tabs_stem_fragment_and_local_assets(client):
    project = client.post('/api/projects', json={'name': '서버 렌더링'}).json()
    stored = client.app.state.store.get(project['id'])
    stored['tracks'] = [{'id': 'safe-id', 'name': '<img src=x onerror=alert(1)>.wav',
                        'status': 'ready', 'duration': 1, 'offset': 0, 'sample_rate':44100,
                        'separation_status':'ready', 'stems':[{'id':'vocal-id','kind':'vocals',
                        'name':'<script>stem</script>', 'status':'pending','duration':1,'sample_rate':44100,'channels':2}]}]
    client.app.state.store.save(stored)
    page = client.get(f"/workspace?project={project['id']}&source=safe-id")
    assert page.status_code == 200
    assert '프로젝트 음원 전환' in page.text and '&lt;img' in page.text
    assert '고급' in page.text and '보컬 분석 켜기' in page.text
    sidebar = page.text.split('aria-label="스튜디오 메뉴"')[1].split('</nav>')[0]
    assert '/analysis' not in sidebar and '/compare' not in sidebar and '/reports' not in sidebar
    fragment = client.get(f"/ui/tracks/{project['id']}?source=safe-id")
    assert fragment.status_code == 200
    assert 'data-track="vocal-id"' in fragment.text
    assert '&lt;script&gt;stem' in fragment.text and '<script>stem' not in fragment.text
    assert client.get(f"/workspace?project={project['id']}&source=missing").status_code == 404
    for path in ['app.js', 'api.js', 'player.js', 'charts.js', 'app.css', 'icons.svg',
                 'reference/theme.css', 'reference/fonts.css']:
        assert client.get('/static/' + path).status_code == 200
