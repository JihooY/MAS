import json
import re
import time
from concurrent.futures import ThreadPoolExecutor
from html.parser import HTMLParser
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import httpx
import pytest
from fastapi.testclient import TestClient
from jinja2 import Environment, nodes

from backend.app.config import Settings
from backend.app.database import Store
from backend.app.i18n import MESSAGES, translate
from backend.app.main import create_app
from backend.app.report_queue import ReportQueue
from backend.app.reports import ai_report, measured_report


class VisibleText(HTMLParser):
    def __init__(self, html):
        super().__init__()
        self.skip = 0
        self.text = []
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        if tag in {'script', 'style'}:
            self.skip += 1

    def handle_endtag(self, tag):
        if tag in {'script', 'style'}:
            self.skip -= 1

    def handle_data(self, data):
        if not self.skip:
            self.text.append(data)


def settings(path):
    return Settings(_env_file=None, storage_dir=path, mongo_uri='', ssh_alias='', admin_email='', admin_password='')


def signup(client):
    return client.post('/register', data={'email': 'language@gmail.com', 'password': 'studio-test-123',
                       'password_confirm': 'studio-test-123'}, follow_redirects=False)


def change_language(client, language, return_to='/'):
    return client.post('/language', data={'language': language, 'return_to': return_to}, follow_redirects=False)


def test_language_persists_through_auth_and_is_isolated_per_browser(tmp_path):
    app = create_app(settings(tmp_path))
    with TestClient(app) as english:
        assert '<html lang="ko">' in english.get('/login').text
        assert english.get('/login').headers['referrer-policy'] == 'same-origin'
        response = change_language(english, 'en', '/register')
        assert response.status_code == 303 and response.headers['location'] == '/register'
        assert 'Max-Age=31536000' in response.headers['set-cookie']
        assert '<html lang="en">' in english.get('/register').text
        assert 'Create account' in english.get('/register').text
        assert english.get('/api/projects').json()['detail'] == 'Please sign in.'
        assert signup(english).status_code == 303
        english_page = english.get('/dashboard').text
        assert 'My projects' in english_page and 'Display language' in english_page
        assert 'data-settings-pane="language"' in english_page
        assert 'Choose music files' in english.get('/workspace').text
        korean = TestClient(app)
        try:
            korean.cookies.set('mas_session', english.cookies.get('mas_session'))
            with ThreadPoolExecutor(max_workers=2) as pool:
                en, ko = list(pool.map(lambda c: c.get('/dashboard').text, [english, korean]))
            assert '<html lang="en">' in en and 'My projects' in en
            assert '<html lang="ko">' in ko and '나의 프로젝트' in ko
        finally:
            korean.close()
        assert english.get('/api/projects/missing').json()['detail'] == 'Project not found.'
        assert english.post('/logout', follow_redirects=False).status_code == 303
        assert 'Welcome back' in english.get('/login').text
        error = english.post('/login', data={'email': 'language@gmail.com', 'password': 'wrong'})
        assert error.status_code == 401 and 'The email or password is incorrect.' in error.text
        assert change_language(english, 'ko').status_code == 303
        assert '다시 만나 반가워요' in english.get('/login').text


@pytest.mark.parametrize('return_to', ['https://evil.example', '//evil.example', '/\\evil.example', '//[', '/\n/evil.example'])
def test_language_redirects_stay_local_and_validate_options(tmp_path, return_to):
    with TestClient(create_app(settings(tmp_path))) as client:
        response = change_language(client, 'en', return_to)
        assert response.status_code == 303 and response.headers['location'] == '/'
        assert change_language(client, 'fr').status_code == 422
        assert client.post('/language', data={'language': 'ko'}, headers={'Origin': 'https://evil.example'}).status_code == 403
        assert client.post('/language', data={'language': 'ko'}, headers={'Origin': 'null'}).status_code == 403
        response = change_language(client, 'ko', '/workspace?project=abc&source=def')
        assert response.headers['location'] == '/workspace?project=abc&source=def'


def test_english_templates_translate_system_text_and_preserve_user_content(tmp_path):
    app = create_app(settings(tmp_path))
    with TestClient(app) as client:
        change_language(client, 'en')
        signup(client)
        project = client.post('/api/projects', json={'name': '음악 추가'}).json()
        project['tracks'] = [{'id': 's', 'name': '나의 프로젝트.wav', 'duration': 1, 'status': 'ready',
            'separation_status': 'ready', 'sample_rate': 44100, 'stems': [
                {'id': 'v', 'name': '보컬', 'kind': 'vocals', 'duration': 1, 'sample_rate': 44100, 'status': 'ready'},
                {'id': 'd', 'name': '변형 보컬 (+2반음 · 1.1×)', 'kind': 'vocals', 'derived': True,
                 'duration': 1, 'sample_rate': 44100, 'status': 'ready'}]}]
        app.state.store.save(project)
        for path in ['/dashboard', f'/workspace?project={project["id"]}', f'/ui/tracks/{project["id"]}']:
            html = client.get(path)
            assert html.status_code == 200
            text = ''.join(VisibleText(html.text).text)
            if '/workspace' in path:
                assert '음악 추가' in text and '나의 프로젝트.wav' in text
                assert 'Vocals' in text and 'Vocal variant (+2 semitones · 1.1×)' in text
            # User names and native language option remain Korean; all system text is translated.
            remaining = text.replace('나의 프로젝트.wav', '').replace('음악 추가', '').replace('한국어', '')
            assert not re.search('[가-힣]', remaining), remaining
        malicious = '<script>alert("name")</script>'
        client.patch(f'/api/projects/{project["id"]}', json={'name': malicious})
        html = client.get('/dashboard').text
        assert '&lt;script&gt;' in html and malicious not in html


def test_translation_catalog_covers_templates_and_interpolation():
    root = Path(__file__).resolve().parents[1] / 'backend/app/templates'
    for path in root.rglob('*.html'):
        tree = Environment().parse(path.read_text(encoding='utf-8'))
        for call in tree.find_all(nodes.Call):
            if isinstance(call.node, nodes.Name) and call.node.name == 't' and call.args and isinstance(call.args[0], nodes.Const):
                assert call.args[0].value in MESSAGES, (path, call.args[0].value)
    for source, target in MESSAGES.items():
        assert set(re.findall(r'\{(\w+)\}', source)) == set(re.findall(r'\{(\w+)\}', target))
    assert translate('파일당 최대 150MB까지 업로드할 수 있습니다.', 'en') == 'You can upload up to 150 MB per file.'
    assert translate('드럼 트랙 저장', 'en') == 'Saving Drums stem'
    assert translate('변형 보컬 (+2반음 · 1.1×)', 'en') == 'Vocal variant (+2 semitones · 1.1×)'
    assert translate('실행 중 {running} · 대기 {queued}', 'en', running=0, queued=2) == 'Running 0 · Queued 2'


def item():
    return {'name': '원본 파일.wav', 'source_id': 's', 'track_id': 'v',
            'region': {'median_pitch_hz': 220, 'mean_rms_db': -20, 'valid_pitch_frames': 5, 'total_pitch_frames': 10}}


def test_reports_use_submission_language_and_queue_preserves_it_on_restart(tmp_path):
    class Executor:
        def __init__(self):
            self.calls = []

        def submit(self, callback, *args):
            self.calls.append((callback, args))

    config = settings(tmp_path)
    config.prepare()
    store = Store(config)
    project = {'id': 'p', 'name': 'p', 'updated_at': '2026', 'tracks': []}
    store.save(project)
    queue = ReportQueue(store, config, Executor())
    english = queue.submit(project, [item()], 0, 1, False, language='en')
    korean = queue.submit(project, [item()], 0, 1, False, enqueue=True, language='ko')
    assert english['id'] != korean['id']
    assert queue.submit(project, [item()], 0, 1, False, language='en')['reused']
    executor = Executor()
    recovered = ReportQueue(Store(config), config, executor)
    recovered.recover()
    for callback, args in executor.calls:
        callback(*args)
    assert recovered.get(english['id'])['result']['language'] == 'en'
    assert '## Vocal measurement report' in recovered.get(english['id'])['result']['content']
    assert '원본 파일' in recovered.get(english['id'])['result']['content']
    assert '## 보컬 측정 리포트' in recovered.get(korean['id'])['result']['content']
    report = measured_report([item(), item()], 0, 1, language='en')
    assert 'mean frame RMS difference' in report and '### Interpreting the results' in report


def test_report_api_uses_language_cookie_and_keeps_saved_report_language(tmp_path):
    app = create_app(settings(tmp_path))
    with TestClient(app) as client:
        change_language(client, 'en')
        signup(client)
        project = client.post('/api/projects', json={'name': 'report project'}).json()
        project['tracks'] = [{'id': 's', 'name': 'song.wav', 'duration': 1, 'status': 'ready',
                              'separation_status': 'ready', 'stems': [{'id': 'v', 'kind': 'vocals',
                              'name': '보컬', 'duration': 1, 'status': 'ready'}]}]
        app.state.store.save(project)
        analysis = {'summary': {}, 'vibrato_candidates': [], 'series': {'time': [.1, .5],
                    'pitch_time': [.1, .5], 'pitch_hz': [220, 220], 'pitch_midi': [57, 57], 'rms_db': [-20, -20]}}
        (tmp_path / 'cache/result-v.json').write_text(json.dumps(analysis), encoding='utf-8')
        response = client.post(f'/api/projects/{project["id"]}/reports', json={'start': 0, 'end': 1})
        assert response.status_code == 202 and response.json()['language'] == 'en'
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            job = client.get(f'/api/jobs/{response.json()["id"]}').json()
            if job['status'] in {'done', 'failed'}:
                break
            time.sleep(.01)
        assert job['status'] == 'done'
        assert job['result']['content'].startswith('## Vocal measurement report')
        assert 'song' in job['result']['content'] and 'Vocals' in job['result']['content']
        change_language(client, 'ko')
        saved = client.get(f'/api/projects/{project["id"]}/reports/0').json()
        assert saved['language'] == 'en' and saved['content'] == job['result']['content']


def test_english_ai_prompt_keeps_evidence_and_safety_instructions():
    seen = []

    def respond(request):
        seen.append(json.loads(request.content))
        return httpx.Response(200, json={'choices': [{'message': {'content': '## Summary\nMeasured feedback.'}}]})

    real_client = httpx.Client
    config = SimpleNamespace(ai_url='http://provider', ai_model='model', ai_api_key='')
    with patch('backend.app.reports.httpx.Client', side_effect=lambda **kw: real_client(transport=httpx.MockTransport(respond), **kw)):
        assert ai_report(config, [item()], language='en').startswith('## Summary')
    system = seen[0]['messages'][0]['content']
    assert 'English report' in system and 'untrusted filenames' in system
    assert 'not listened to the audio' in system and 'Do not rank singers' in system
    assert json.loads(seen[0]['messages'][1]['content']) == [item()]
