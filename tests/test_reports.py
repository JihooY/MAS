import json
from unittest.mock import patch

import httpx
from backend.app.reports import ai_report
from backend.app.config import Settings
from tests.music_fakes import item, observations, response_for


def test_final_llm_interprets_three_types_then_integrates_and_scores_without_audio_bytes():
    seen = []
    def respond(request):
        body = json.loads(request.content)
        seen.append(body)
        return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps(response_for(body))}}]})
    real_client = httpx.Client
    config = Settings(_env_file=None, llm_ai_url="http://model/v1", llm_ai_api_key="test-key")
    with patch("backend.app.ai_provider.httpx.Client", side_effect=lambda **kw: real_client(transport=httpx.MockTransport(respond), **kw)):
        result = ai_report(config, [item()], audio_analyses=observations())
    assert [json.loads(b['messages'][1]['content'])['stage'] for b in seen] == ['full_mix', 'vocals', 'instrumental', 'integrated']
    assert result['scoring']['sources'][0]['overall'] == 8.0
    assert set(result['views']) == {'full_mix', 'vocals', 'instrumental', 'integrated'}
    assert 'input_audio' not in json.dumps(seen)
    assert '보컬과 음악의 조화' in result['content']
