from copy import deepcopy
import json

import httpx
import numpy as np
import pytest
import soundfile as sf

from backend.app import ai_provider
from backend.app.ai_provider import AIError
from backend.app.config import Settings
from backend.app.music_ai import chunk_ranges, pcm_segment
from backend.app.music_evaluation import context_index, calculate_scores, evaluate
from tests.music_fakes import item, observations, response_for


def valid_assessments():
    values = observations()
    catalog = [{"id": o['evidence_id'], 'source_id': 's', 'kind': o['kind'], 'scope': 'section'} for o in values]
    catalog += [{'id': 'dsp:v', 'source_id': 's', 'kind': 'dsp', 'scope': 'section'}]
    payload = {'stage': 'integrated', 'dsp_evidence': [item()], 'full_mix_context': [values[0]], 'evidence_catalog': catalog}
    return response_for({'messages': [{}, {'content': json.dumps(payload)}]})['assessments']


def test_weighted_score_is_computed_on_backend_and_null_is_not_zero():
    values = valid_assessments()
    scores = values[0]['scores']
    scores['music_fit']['value'] = 9
    scores['technique']['value'] = 6
    # 9*.25 + 8*.15 + 6*.20 + 8*.20 + 8*.10 + 8*.10 = 7.85 -> 7.9
    index = context_index([item()], observations())
    result = calculate_scores(values, [item()], index)['sources'][0]
    assert result['overall'] == 7.9 and result['coverage'] == 1
    scores['technique']['value'] = None
    result = calculate_scores(values, [item()], index)['sources'][0]
    assert result['overall'] is None and result['coverage'] == .8
    assert result['status'] == 'insufficient_evidence'


def test_full_mix_must_exist_for_every_aligned_interval():
    for replacement in [observations()[1:], observations()+[observations()[0]]]:
        with pytest.raises(AIError):
            context_index([item()], replacement)
    changed = observations()
    changed[1]['audio']['source_range_seconds'] = [.2, .8]
    with pytest.raises(AIError, match='aligned'):
        context_index([item()], changed)


def test_scores_cannot_cite_other_recordings_or_omit_full_mix():
    index = context_index([item()], observations())
    values = valid_assessments()
    values[0]['scores']['music_fit']['evidence_ids'] = ['audio:s:vocals']
    with pytest.raises(AIError, match='required'):
        calculate_scores(values, [item()], index)
    values[0]['scores']['music_fit']['evidence_ids'] = ['invented-recording']
    with pytest.raises(AIError, match='unrelated'):
        calculate_scores(values, [item()], index)


@pytest.mark.parametrize('failure', ['score_11', 'score_bool', 'score_nan', 'interaction_no_mix', 'wrong_source', 'wrong_overview', 'wrong_type'])
def test_invalid_llm_scoring_or_grounding_fails_and_preserves_raw_stage(tmp_path, monkeypatch, failure):
    def respond(request):
        body = json.loads(request.content)
        parsed = response_for(body)
        stage = json.loads(body['messages'][1]['content'])['stage']
        if stage == 'integrated':
            if failure.startswith('score_'):
                parsed['assessments'][0]['scores']['music_fit']['value'] = {'score_11': 11, 'score_bool': True, 'score_nan': float('nan')}[failure]
            elif failure == 'interaction_no_mix':
                parsed['interactions'][0]['evidence_ids'] = ['audio:s:vocals']
            elif failure == 'wrong_source':
                parsed['assessments'][0]['source_id'] = 'unheard-source'
            elif failure == 'wrong_overview':
                parsed['song_overviews'][0]['evidence_ids'] = ['dsp:v']
        elif failure == 'wrong_type':
            parsed['analyses'][0]['evidence_ids'] = ['dsp:v']
        return httpx.Response(200, json={'choices': [{'message': {'content': json.dumps(parsed)}}]})
    real_client = httpx.Client
    monkeypatch.setattr(ai_provider.httpx, 'Client', lambda **kw: real_client(transport=httpx.MockTransport(respond), **kw))
    stages = {}
    config = Settings(_env_file=None, llm_ai_url='http://text/v1', llm_ai_api_key='test')
    with pytest.raises(AIError):
        evaluate(config, [item()], observations(), on_stage=lambda k, v: stages.update({k: v}))
    stage = 'full_mix' if failure == 'wrong_type' else 'integrated'
    assert stages[stage]['status'] == 'received' and stages[stage]['raw_response']['content']


def test_named_boundaries_are_used_without_losing_frames_and_instrumental_sums_stems(tmp_path):
    sr = 24000
    a, b = tmp_path/'drums.wav', tmp_path/'bass.wav'
    sf.write(a, np.full(sr*3, .1), sr, subtype='FLOAT')
    sf.write(b, np.full(sr*3, .2), sr, subtype='FLOAT')
    plan = list(chunk_ranges(a, 0, 3, max_seconds=1, sections=[{'start': .4, 'end': 1.8}]))
    assert .4 in [end for _, end, _ in plan] and 1.8 in [end for _, end, _ in plan]
    assert all(abs(plan[i][1]-plan[i+1][0]) < 1e-8 for i in range(len(plan)-1))
    assert plan[0][0] == 0 and plan[-1][1] == 3 and all(end-start<=1 for start,end,_ in plan)
    samples, meta = pcm_segment([a,b], .2, .7)
    assert np.allclose(samples, .3) and len(samples) == sr//2 and meta['stem_count'] == 2
    sf.write(b, np.zeros(sr), sr//2)
    with pytest.raises(AIError, match='mismatched'):
        pcm_segment([a,b], .2, .7)
