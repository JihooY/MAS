import base64
from io import BytesIO
import json
from pathlib import Path
import time

import httpx
import numpy as np
import pytest
import soundfile as sf
from fastapi.testclient import TestClient

from backend.app import ai_provider, separation
from backend.app.ai_provider import AIError, Provider, endpoint_urls
from backend.app.config import Settings
from backend.app.main import create_app
from backend.app.music_ai import audio_inputs, wav_chunks
from backend.check_ai import check


def config(path, **values):
    return Settings(_env_file=None, storage_dir=path, mongo_uri='', ssh_alias='', admin_email='', admin_password='',
                    audio_ai_url='http://flamingo/v1/chat/completions', audio_ai_api_key='secret-audio-key',
                    llm_ai_url='http://gpt-oss/v1', llm_ai_api_key='secret-llm-key', **values)


def use_transport(monkeypatch, respond):
    real_client = httpx.Client
    monkeypatch.setattr(ai_provider.httpx, 'Client',
                        lambda **kw: real_client(transport=httpx.MockTransport(respond), **kw))


def test_env_loads_independent_roles_and_hides_keys(tmp_path):
    env = tmp_path / '.env'
    env.write_text('AUDIO_AI_URL=https://music/v1\nAUDIO_AI_MODEL=served-music\nAUDIO_AI_API_KEY=secret-one\n'
                   'LLM_AI_URL=https://text/v1/chat/completions\nLLM_AI_MODEL=served-oss\nLLM_AI_API_KEY=secret-two\n')
    s = Settings(_env_file=env)
    assert (s.audio_ai_url, s.audio_ai_model, s.audio_ai_api_key) == ('https://music/v1', 'served-music', 'secret-one')
    assert (s.llm_ai_url, s.llm_ai_model, s.llm_ai_api_key) == ('https://text/v1/chat/completions', 'served-oss', 'secret-two')
    assert 'secret-one' not in repr(s) and 'secret-two' not in repr(s)
    assert 'secret-one' not in repr(Provider.from_settings(s, 'AUDIO'))


@pytest.mark.parametrize('url', ['http://server', 'http://server/v1/', 'http://server/v1/chat/completions'])
def test_endpoint_supports_base_or_full_url(url):
    assert endpoint_urls(url) == ('http://server/v1/chat/completions', 'http://server/v1/models')


@pytest.mark.parametrize('url', ['file:///tmp/key', 'https://user:secret@host/v1', 'https://host/v1?key=secret'])
def test_endpoint_rejects_credentials_in_urls(url):
    with pytest.raises(AIError):
        endpoint_urls(url)


def test_real_wav_selection_covers_range_without_truncating_or_changing_gain(tmp_path):
    path = tmp_path / 'music.wav'
    sr = 44100
    samples = .1 * np.sin(2 * np.pi * 220 * np.arange(sr * 3) / sr)
    sf.write(path, np.column_stack([samples, samples]), sr)
    chunks = list(wav_chunks(path, .25, 2.75, max_seconds=1))
    assert len(chunks) == 3
    assert chunks[0][1]['source_range_seconds'][0] == .25
    assert chunks[-1][1]['source_range_seconds'][1] == 2.75
    assert all(chunks[i][1]['source_frames'][1] == chunks[i+1][1]['source_frames'][0] for i in range(2))
    for data, meta in chunks:
        info = sf.info(BytesIO(data))
        assert info.subtype == 'PCM_16' and info.channels == 1 and info.samplerate == 24000
        assert info.duration <= 1
        decoded, _ = sf.read(BytesIO(data))
        assert .09 < np.max(np.abs(decoded)) < .11
        assert len(meta['sha256']) == 64
    with pytest.raises(AIError):
        list(wav_chunks(path, 0, 4))


def test_derived_vocal_does_not_claim_original_alignment_and_prevents_path_escape(tmp_path):
    s = config(tmp_path)
    s.prepare()
    sf.write(tmp_path / 'audio/v.wav', np.zeros(24000), 24000)
    project = {'tracks': [{'id': 's', 'original_file': 'original.wav', 'stems': [
        {'id': 'v', 'file': 'v.wav', 'derived': True, 'transform': {'speed': 2}}]}]}
    item = {'track_id': 'v', 'source_id': 's', 'name': 'song', 'range_seconds': [0, 1],
            'source_range_seconds': [0, 1], 'offset_seconds': 0}
    entries = audio_inputs(s, project, [item])
    assert [e['kind'] for e in entries] == ['vocals'] and entries[0]['derived']
    project['tracks'][0]['stems'][0]['file'] = '../outside.wav'
    with pytest.raises(AIError):
        audio_inputs(s, project, [item])


@pytest.mark.parametrize('failure', ['model', 'audio', 'llm', 'timeout', 'empty', 'length', 'json'])
def test_provider_failures_are_explicit_and_keys_are_redacted(tmp_path, monkeypatch, failure):
    s = config(tmp_path)
    s.audio_ai_api_key = ' ' + s.audio_ai_api_key + ' '
    s.llm_ai_api_key = ' ' + s.llm_ai_api_key + ' '
    def respond(request):
        if failure == 'model':
            return httpx.Response(200, json={'data': [{'id': 'a-different-model'}]})
        if failure == 'timeout':
            raise httpx.ReadTimeout('secret-audio-key', request=request)
        if failure == 'json':
            return httpx.Response(200, text='<html>proxy error</html>')
        if failure in {'audio', 'llm'}:
            return httpx.Response(400, json={'error': {'message': 'input_audio unsupported secret-audio-key secret-llm-key'}})
        return httpx.Response(200, json={'choices': [{'message': {'content': '' if failure == 'empty' else 'partial'},
                                                     'finish_reason': 'length' if failure == 'length' else 'stop'}]})
    use_transport(monkeypatch, respond)
    provider = Provider.from_settings(s, 'AUDIO')
    with pytest.raises(AIError) as error:
        provider.check_model(s) if failure == 'model' else provider.complete(s, [], 1024)
    assert 'secret-audio-key' not in str(error.value) and 'secret-llm-key' not in str(error.value)
    assert str(error.value)


def wait_job(client, jid):
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        result = client.get('/api/jobs/' + jid).json()
        if result['status'] in {'done', 'failed'}:
            return result
        time.sleep(.01)
    pytest.fail('Job timed out')


@pytest.mark.parametrize('failed_stage', [None, 'audio', 'llm'])
def test_upload_to_listening_to_report_and_partial_failure_persistence(tmp_path, monkeypatch, caplog, failed_stage):
    s = config(tmp_path)
    # Only expensive Demucs weights are replaced. Decode, files, DSP, queue and HTTP payloads are real.
    def separate(path, out, model, device, models, progress):
        out.mkdir(parents=True)
        samples, sr = sf.read(path, always_2d=True)
        sf.write(out / 'vocals.wav', samples, sr)
        return [{'kind': 'vocals', 'name': '보컬', 'duration': len(samples)/sr, 'sample_rate': sr}]
    monkeypatch.setattr(separation, 'separate', separate)
    app = create_app(s)
    seen = []
    with TestClient(app) as client:
        credentials = {'email': 'music@gmail.com', 'password': 'music-test-password'}
        assert client.post('/register', data={**credentials, 'password_confirm': credentials['password']}).status_code == 200
        pid = client.post('/api/projects', json={'name': 'Music'}).json()['id']
        data = BytesIO()
        sf.write(data, .1*np.sin(2*np.pi*220*np.arange(24000)/24000), 24000, format='WAV')
        upload = client.post(f'/api/projects/{pid}/tracks', files={'file': ('song.wav', data.getvalue(), 'audio/wav')})
        assert upload.status_code == 202
        sid = upload.json()['track']['id']
        assert wait_job(client, upload.json()['job']['id'])['status'] == 'done'
        route = f'/api/projects/{pid}/tracks/{sid}'
        analysis = client.post(route + '/analyze', json={})
        assert wait_job(client, analysis.json()['id'])['status'] == 'done'
        # Patch only after the browser client is constructed, so its ASGI transport is retained.
        def respond(request):
            seen.append(request)
            audio_role = request.url.host == 'flamingo'
            model = s.audio_ai_model if audio_role else s.llm_ai_model
            assert request.headers['authorization'] == 'Bearer ' + (s.audio_ai_api_key if audio_role else s.llm_ai_api_key)
            if request.method == 'GET':
                assert request.url.path == '/v1/models'
                return httpx.Response(200, json={'data': [{'id': model}]})
            assert request.url.path == '/v1/chat/completions'
            body = json.loads(request.content)
            if audio_role:
                audio = body['messages'][0]['content'][1]['input_audio']
                pcm = base64.b64decode(audio['data'])
                info = sf.info(BytesIO(pcm))
                assert audio['format'] == 'wav' and info.subtype == 'PCM_16'
                assert .49 < info.duration < .51
                if failed_stage == 'audio':
                    return httpx.Response(400, json={'error': {'message': 'Unsupported input_audio secret-audio-key'}})
            else:
                evidence = json.loads(body['messages'][1]['content'])
                assert len(evidence['music_flamingo']) == 2
                assert evidence['dsp_evidence'][0]['source_range_seconds'] == [.2, .7]
                assert 'input_audio' not in request.content.decode()
                if failed_stage == 'llm':
                    return httpx.Response(503, json={'error': {'message': 'Unavailable secret-llm-key'}})
            return httpx.Response(200, json={'model': model, 'choices': [{'message': {
                'content': 'A quiet sustained vocal.' if audio_role else '## 요약\n실제 청취와 측정에 근거한 해석'}, 'finish_reason': 'stop'}]})
        use_transport(monkeypatch, respond)
        response = client.post(f'/api/projects/{pid}/reports', json={'start': .2, 'end': .7, 'ai': True})
        assert response.status_code == 202
        result = wait_job(client, response.json()['id'])
        assert result['status'] == ('failed' if failed_stage else 'done')
        assert 'secret-audio-key' not in json.dumps(result) + caplog.text
        assert 'secret-llm-key' not in json.dumps(result) + caplog.text
        assert 'secret-audio-key' not in client.get('/api/health').text
        if failed_stage == 'audio':
            assert not any(r.method == 'POST' and r.url.host == 'gpt-oss' for r in seen)
            assert 'HTTP 400' in result['message']
        elif failed_stage == 'llm':
            assert len(result['partial_audio_analyses']) == 2
            assert result['validation']['AUDIO']['audio_input_verified']
        else:
            assert result['result']['validation']['AUDIO']['audio_input_verified']
            assert len(result['result']['audio_analyses']) == 2
            assert result['result']['expert_feedback'] == []
            assert len(seen) == 5
            saved = client.get(f'/api/projects/{pid}/reports/0').json()
            assert saved['audio_model'] == s.audio_ai_model and saved['model'] == s.llm_ai_model
        # Both playback and DSP comparison endpoints remain functional.
        assert client.get(route + '/audio').status_code == 200
        assert client.get(route + '/analysis').status_code == 200
        persisted = app.state.store.report_jobs()[0]
        assert persisted['status'] == result['status']
        assert 'base64' not in json.dumps(persisted.get('audio_analyses', []))
        assert s.audio_ai_api_key not in json.dumps(persisted)


def test_diagnostic_does_not_claim_audio_support_from_models_alone(tmp_path, monkeypatch):
    s = config(tmp_path)
    def respond(request):
        return httpx.Response(200, json={'data': [{'id': s.audio_ai_model}, {'id': s.llm_ai_model}]})
    use_transport(monkeypatch, respond)
    result = check(s)
    assert all(p['status'] == 'ok' for p in result['providers'].values())
    assert result['audio_test']['status'] == result['llm_test']['status'] == 'not_tested'


def test_diagnostic_sends_short_wav_then_final_interpretation(tmp_path, monkeypatch):
    s = config(tmp_path)
    path = tmp_path / 'music.wav'
    sf.write(path, .1*np.sin(2*np.pi*440*np.arange(24000)/24000), 24000)
    seen = []
    def respond(request):
        seen.append(request)
        audio_role = request.url.host == 'flamingo'
        model = s.audio_ai_model if audio_role else s.llm_ai_model
        if request.method == 'GET':
            return httpx.Response(200, json={'data': [{'id': model}]})
        body = json.loads(request.content)
        if audio_role:
            data = base64.b64decode(body['messages'][0]['content'][1]['input_audio']['data'])
            assert sf.info(BytesIO(data)).duration == 1
        else:
            assert json.loads(body['messages'][1]['content'])['dsp_evidence'] == []
        return httpx.Response(200, json={'choices': [{'message': {'content': 'Audible sustained tone.'}}]})
    use_transport(monkeypatch, respond)
    result = check(s, path)
    assert result['audio_test']['status'] == result['llm_test']['status'] == 'ok'
    assert len(seen) == 4
    assert not list((tmp_path / 'cache').glob('mas-ai-check-*'))
