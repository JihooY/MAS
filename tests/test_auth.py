import time
from concurrent.futures import ThreadPoolExecutor

import mongomock
import pytest
from fastapi.testclient import TestClient

from backend.app.auth import digest
from backend.app.config import Settings
from backend.app.database import Store
from backend.app.main import create_app

PASSWORD = 'studio-test-password-123'


def config(path, **values):
    return Settings(_env_file=None, storage_dir=path, mongo_uri='', ssh_alias='',
                    admin_email='', admin_password='', **values)


def signup(client, email='alice@gmail.com'):
    return client.post('/register', data={'email': email, 'password': PASSWORD,
                       'password_confirm': PASSWORD}, follow_redirects=False)


def login(client, email='alice@gmail.com', password=PASSWORD):
    return client.post('/login', data={'email': email, 'password': password}, follow_redirects=False)


@pytest.fixture(params=['json', 'mongodb'])
def app(tmp_path, monkeypatch, request):
    if request.param == 'mongodb':
        db = mongomock.MongoClient().db
        monkeypatch.setattr(Store, 'connect', lambda store: setattr(store, 'collection', db.projects))
    return create_app(config(tmp_path))


def test_registration_login_validation_and_revoked_cookie(app):
    with TestClient(app) as client:
        for path in ['/', '/dashboard', '/docs', '/openapi.json']:
            response = client.get(path, follow_redirects=False)
            assert response.status_code == 303 and response.headers['location'] == '/login'
        for path in ['/api/projects', '/api/health', '/api/jobs/unknown', '/api/auth/me', '/ui/tracks/p']:
            assert client.get(path).status_code == 401
        assert client.get('/static/app.css').status_code == 200
        assert 'MAS 전용 비밀번호' in client.get('/register').text
        for email, password, confirm in [('alice@example.com', PASSWORD, PASSWORD),
                                          ('alice@gmail.com', 'short', 'short'),
                                          ('alice@gmail.com', PASSWORD, 'mismatch')]:
            assert client.post('/register', data={'email': email, 'password': password,
                               'password_confirm': confirm}).status_code == 422
        response = signup(client, 'A.Lice+studio@gmail.com')
        assert response.status_code == 303
        assert 'HttpOnly' in response.headers['set-cookie'] and 'SameSite=lax' in response.headers['set-cookie']
        assert client.get('/api/auth/me').json()['email'] == 'alice@gmail.com'
        assert client.get('/api/auth/me').json()['role'] == 'user'
        assert signup(client).status_code == 409
        assert 'no-store' in client.get('/').headers['cache-control']
        old_cookie = client.cookies.get('mas_session')
        assert client.post('/logout', follow_redirects=False).status_code == 303
        client.cookies.set('mas_session', old_cookie)
        assert client.get('/api/projects').status_code == 401  # Revoked token replay.
        client.cookies.clear()
        assert login(client, password='wrong-password').status_code == 401
        assert login(client).status_code == 303
        assert client.cookies.get('mas_session') != old_cookie
        users = app.state.store.identity_records('users')
        assert len(users) == 1 and PASSWORD not in str(users)
        assert users[0]['password_hash'].startswith('pbkdf2_sha256$600000$')
        session = app.state.store.identity_get('auth_sessions', digest(client.cookies.get('mas_session')))
        session['expires_at'] = time.time() - 1
        app.state.store.identity_save('auth_sessions', session)
        assert client.get('/api/projects').status_code == 401


def test_accounts_cannot_access_each_others_data(app):
    with TestClient(app) as alice, TestClient(app) as bob:
        assert signup(alice).status_code == 303
        assert signup(bob, 'bob@gmail.com').status_code == 303
        project = alice.post('/api/projects', json={'name': 'Alice private song'}).json()
        pid = project['id']
        assert bob.get('/api/projects').json() == []
        assert alice.get('/api/projects').json()[0]['id'] == pid
        for path in [f'/workspace?project={pid}', f'/dashboard?project={pid}', f'/ui/tracks/{pid}',
                     f'/api/projects/{pid}', f'/api/projects/{pid}/reports/0',
                     f'/api/projects/{pid}/tracks/t/audio', f'/api/projects/{pid}/tracks/t/original',
                     f'/api/projects/{pid}/tracks/t/analysis', f'/api/projects/{pid}/tracks/t/stems/v/audio',
                     f'/api/projects/{pid}/tracks/t/stems/v/analysis']:
            assert bob.get(path).status_code == 404, path
        for method, path, kwargs in [('patch', f'/api/projects/{pid}', {'json': {'name': 'stolen'}}),
                ('delete', f'/api/projects/{pid}', {}),
                ('post', f'/api/projects/{pid}/tracks', {'files': {'file': ('x.wav', b'bad')}}),
                ('post', f'/api/projects/{pid}/reports', {'json': {'start': 0, 'end': 1}}),
                ('post', f'/api/projects/{pid}/tracks/t/separate', {'json': {'model': 'htdemucs'}}),
                ('post', f'/api/projects/{pid}/tracks/t/analyze', {'json': {}}),
                ('post', f'/api/projects/{pid}/tracks/t/transform', {'json': {}}),
                ('patch', f'/api/projects/{pid}/tracks/t', {'json': {'offset': 1}}),
                ('delete', f'/api/projects/{pid}/tracks/t', {})]:
            assert getattr(bob, method)(path, **kwargs).status_code == 404
        assert 'Alice private song' not in bob.get('/').text
        app.state.jobs['secret-audio-job'] = {'id': 'secret-audio-job', 'project_id': pid, 'status': 'done'}
        app.state.report_queue.jobs['secret-report-job'] = {
            'id': 'secret-report-job', 'project_id': pid, 'project_name': 'Alice private song',
            'created_at': '2026', 'status': 'done', 'result': {'content': 'private report'}}
        for jid in ['secret-audio-job', 'secret-report-job']:
            assert bob.get(f'/api/jobs/{jid}').status_code == 404
            assert alice.get(f'/api/jobs/{jid}').status_code == 200
        snapshot = bob.get('/api/report-jobs').json()
        assert snapshot['jobs'] == [] and snapshot['recent'] == []
        assert len(alice.get('/api/report-jobs').json()['recent']) == 1


def test_session_survives_restart_and_signup_race(tmp_path):
    settings = config(tmp_path)
    with TestClient(create_app(settings)) as first:
        with ThreadPoolExecutor(max_workers=2) as pool:
            responses = list(pool.map(lambda _: signup(first), range(2)))
        assert sorted(r.status_code for r in responses) == [303, 409]
        cookie = first.cookies.get('mas_session')
        uid = first.get('/api/auth/me').json()['user_id']
    with TestClient(create_app(settings)) as second:
        second.cookies.set('mas_session', cookie)
        assert second.get('/api/auth/me').json()['user_id'] == uid


def test_admin_bootstrap_migration_rotation_and_removal(tmp_path):
    settings = config(tmp_path)
    settings.admin_email = 'admin@gmail.com'
    settings.admin_password = PASSWORD
    settings.prepare()
    Store(settings).save({'id': 'legacy', 'name': 'Old shared project', 'updated_at': '2026', 'tracks': []})
    with TestClient(create_app(settings)) as client:
        assert login(client, 'admin@gmail.com').status_code == 303
        user = client.get('/api/auth/me').json()
        assert user['role'] == 'admin'
        assert client.get('/api/projects/legacy').json()['owner_id'] == user['user_id']
        cookie = client.cookies.get('mas_session')
        assert signup(client, 'regular@gmail.com').status_code == 303
        assert client.get('/api/projects').json() == []
    settings.admin_password = 'changed-admin-password-456'
    with TestClient(create_app(settings)) as client:
        client.cookies.set('mas_session', cookie)
        assert client.get('/api/projects').status_code == 401
        assert login(client, 'admin@gmail.com', settings.admin_password).status_code == 303
        assert client.get('/api/auth/me').json()['user_id'] == user['user_id']
        assert login(client, 'admin@gmail.com', PASSWORD).status_code == 401
    settings.admin_email = settings.admin_password = ''
    with TestClient(create_app(settings)) as client:
        assert login(client, 'admin@gmail.com', 'changed-admin-password-456').status_code == 401
        assert login(client, 'regular@gmail.com').status_code == 303


def test_origin_checks_throttling_and_secure_cookie(tmp_path):
    settings = config(tmp_path)
    settings.session_https_only = True
    with TestClient(create_app(settings), base_url='https://testserver') as client:
        for headers in [{'Origin': 'https://evil.example'}, {'Sec-Fetch-Site': 'cross-site'},
                        {'Referer': 'https://evil.example/form'}]:
            assert client.post('/register', data={'email': 'alice@gmail.com', 'password': PASSWORD,
                               'password_confirm': PASSWORD}, headers=headers).status_code == 403
        response = signup(client)
        assert 'Secure' in response.headers['set-cookie']
        assert '__Host-mas_session=' in response.headers['set-cookie']
        assert client.get('/api/auth/me').status_code == 200
        for _ in range(9):
            assert login(client, password='incorrect').status_code == 401
        limited = login(client)
        assert limited.status_code == 429 and int(limited.headers['retry-after']) > 0


def test_unowned_data_stays_hidden_without_admin(tmp_path):
    settings = config(tmp_path)
    settings.prepare()
    Store(settings).save({'id': 'legacy', 'name': 'unclaimed', 'updated_at': '2026', 'tracks': []})
    with TestClient(create_app(settings)) as client:
        assert signup(client).status_code == 303
        assert client.get('/api/projects').json() == []
        assert client.get('/api/projects/legacy').status_code == 404
