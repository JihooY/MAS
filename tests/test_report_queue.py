import json
from concurrent.futures import ThreadPoolExecutor
from threading import Event
import time
import numpy as np
import soundfile as sf

import mongomock
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from backend.app.config import Settings
from backend.app.database import Store
from backend.app.main import create_app
from backend.app.report_queue import ReportQueue
from backend.app import report_queue as queue_module
from backend.app.ai_provider import AIError


def settings(path):
    return Settings(_env_file=None, storage_dir=path, mongo_uri='', ssh_alias='',
                    audio_ai_url='http://audio-provider/v1', audio_ai_api_key='audio-key',
                    llm_ai_url='http://test-provider/v1', llm_ai_model='test-model', llm_ai_api_key='llm-key')


def ready_project(app, name):
    project = {'id': name, 'name': name, 'schema_version': 2, 'created_at': '2026',
               'owner_id': app.state.auth.store.identity_records('users')[0]['user_id'],
               'updated_at': '2026', 'tracks': [{'id': 'source', 'name': 'music.wav',
               'status': 'ready', 'duration': 1, 'separation_status': 'ready', 'original_file': name+'-original.wav',
               'stems': [{'id': name+'-vocal', 'name': '보컬', 'kind': 'vocals',
                          'status': 'ready', 'duration': 1, 'file': name+'-vocal.wav'}, {'id': name+'-other', 'kind': 'other', 'file': name+'-other.wav', 'status': 'ready'}]}]}
    for kind in ('vocal', 'original', 'other'):
        sf.write(app.state.store.settings.storage_dir / 'audio' / (name+'-'+kind+'.wav'), np.zeros(24000), 24000)
    app.state.store.save(project)
    data = {'summary': {'duration': 1}, 'vibrato_candidates': [],
            'series': {'time': [.1, .5, .9], 'rms_db': [-20, -21, -20],
                       'pitch_time': [.1, .5, .9], 'pitch_hz': [220, 220, 220],
                       'pitch_midi': [57, 57, 57]}}
    cache = app.state.store.settings.storage_dir / 'cache' / f'result-{name}-vocal.json'
    cache.write_text(json.dumps(data), encoding='utf-8')
    return project


def terminal(client, jid):
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        job = client.get(f'/api/jobs/{jid}').json()
        if job['status'] in {'done', 'failed'}:
            return job
        time.sleep(.01)
    pytest.fail('report queue did not finish')


def test_clients_share_queue_reject_direct_requests_deduplicate_and_continue_after_failure(tmp_path, monkeypatch):
    entered, release = Event(), Event()
    calls = []

    def provider(config, items, **options):
        calls.append(items[0]['range_seconds'][1])
        if len(calls) == 1:
            entered.set()
            assert release.wait(10)
            raise AIError('simulated provider failure')
        return {'content': '## 분석 완료\n\n| 항목 | 값 |\n| --- | --- |\n| 음높이 | 220 Hz |'}

    monkeypatch.setattr(queue_module, 'ai_report', provider)
    monkeypatch.setattr(queue_module.Provider, 'check_model', lambda self, s: {'model_verified': True})
    monkeypatch.setattr(queue_module, 'listen', lambda *a: [{'content': 'test listening observation'}])
    app = create_app(settings(tmp_path))
    with TestClient(app) as a:
        b = TestClient(app)  # Separate browser/client; the same server lifespan.
        try:
            credentials = {'email': 'queue@gmail.com', 'password': 'test-password-123'}
            assert a.post('/register', data={**credentials, 'password_confirm': credentials['password']}, follow_redirects=False).status_code == 303
            assert b.post('/login', data=credentials, follow_redirects=False).status_code == 303
            ready_project(app, 'project-a')
            ready_project(app, 'project-b')
            request = {'start': 0, 'end': .8, 'ai': True}
            first = a.post('/api/projects/project-a/reports', json=request)
            assert first.status_code == 202
            jid = first.json()['id']
            assert entered.wait(5)
            snapshot = b.get('/api/report-jobs').json()
            assert snapshot == a.get('/api/report-jobs').json()
            assert snapshot['jobs'][0]['id'] == jid
            assert snapshot['jobs'][0]['status'] == 'processing'
            assert 'evidence' not in snapshot['jobs'][0]
            page = b.get('/workspace?project=project-a').text
            assert '공유 분석 진행 상황' in page and 'gpt-oss 최종 보고서 생성 중' in page
            assert b.delete('/api/projects/project-a').status_code == 409

            # Simultaneous submissions from separate clients create no extra work.
            with ThreadPoolExecutor(max_workers=4) as clients:
                duplicates = list(clients.map(lambda _: b.post('/api/projects/project-a/reports', json=request).json(), range(4)))
            assert all(j['id'] == jid and j['reused'] for j in duplicates)
            direct = b.post('/api/projects/project-b/reports', json={**request, 'end': .6})
            assert direct.status_code == 409 and '대기열' in direct.json()['detail']
            second = b.post('/api/projects/project-b/reports', json={**request, 'end': .6, 'enqueue': True})
            assert second.status_code == 202 and second.json()['queue_position'] == 1
            assert calls == [.8]  # Queuing never starts a second provider call.
            assert [j['status'] for j in a.get('/api/report-jobs').json()['jobs']] == ['processing', 'queued']

            release.set()
            assert terminal(a, jid)['status'] == 'failed'
            result = terminal(b, second.json()['id'])
            assert result['status'] == 'done' and '<table>' in result['result']['content_html']
            assert calls == [.8, .6]
            assert not a.get('/api/report-jobs').json()['busy']
            assert a.get('/api/projects/project-b/reports/0').json()['job_id'] == second.json()['id']
            assert a.get(f"/api/jobs/{second.json()['id']}").json() == result
        finally:
            release.set()
            b.close()


class ManualExecutor:
    def __init__(self):
        self.work = []

    def submit(self, callback, *args):
        self.work.append((callback, args))


def item():
    return {'source_id': 'source', 'track_id': 'vocal', 'name': '보컬',
            'region': {'median_pitch_hz': 220, 'mean_rms_db': -20,
                       'valid_pitch_frames': 1, 'total_pitch_frames': 1}}


def test_queue_survives_restart_and_marks_interrupted_call_failed(tmp_path):
    config = settings(tmp_path)
    config.prepare()
    store = Store(config)
    store.save({'id': 'p', 'name': 'p', 'updated_at': '2026', 'tracks': []})
    before = ReportQueue(store, config, ManualExecutor())
    first = before.submit(store.get('p'), [item()], 0, 1, False)
    before._update(before.jobs[first['id']], status='processing')
    second = before.submit(store.get('p'), [item()], 0, 2, False, True)
    runner = ManualExecutor()
    after = ReportQueue(Store(config), config, runner)
    after.recover()
    assert after.get(first['id'])['status'] == 'failed'
    assert after.get(second['id'])['queue_position'] == 1
    assert len(runner.work) == 1
    callback, args = runner.work.pop()
    callback(*args)
    assert after.get(second['id'])['status'] == 'done'
    restored = ReportQueue(Store(config), config, ManualExecutor())
    restored.recover()
    assert restored.get(second['id'])['result']['job_id'] == second['id']
    assert not restored.snapshot()['busy']


def test_queue_capacity_and_mongodb_job_persistence(tmp_path):
    config = settings(tmp_path)
    config.prepare()
    store = Store(config)
    store.collection = mongomock.MongoClient().db.projects
    queue = ReportQueue(store, config, ManualExecutor())
    for i in range(20):
        queue.submit({'id': 'p', 'name': 'p'}, [item()], 0, i+1, False, True)
    with pytest.raises(HTTPException) as error:
        queue.submit({'id': 'p', 'name': 'p'}, [item()], 0, 21, False, True)
    assert error.value.status_code == 409
    assert len(store.report_jobs()) == 20
    assert all('_id' not in j for j in store.report_jobs())
    restored = ReportQueue(store, config, ManualExecutor())
    restored.recover()
    assert [j['queue_position'] for j in restored.snapshot()['jobs']] == list(range(1, 21))
    restored.forget('p')
    assert store.report_jobs() == []
