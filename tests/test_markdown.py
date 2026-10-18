from backend.app.markdown import render_markdown
from backend.app.project import upgrade_project


def test_markdown_headings_tables_lists_and_fences():
    html = render_markdown("## 요약\n\n**근거**\n\n| 항목 | 값 |\n|---|---|\n| RMS | -20 dB |\n\n- 제안\n\n```python\nprint(1)\n```\n\n> 한계")
    for tag in ['<h2>', '<strong>', '<table>', '<ul>', '<pre><code', '<blockquote>']:
        assert tag in html


def test_untrusted_ai_markdown_cannot_execute_html_or_javascript():
    html = render_markdown('<script>alert(1)</script>\n\n<img src=x onerror=alert(1)>\n\n[link](javascript:alert(1))\n\n![tracker](https://evil.example/tracker)')
    assert '<script' not in html and '<img' not in html
    assert 'href="javascript:' not in html and 'onerror="' not in html


def test_legacy_vocal_projects_keep_their_files_and_identity():
    project = {'id': 'project', 'tracks': [{'id':'old','name':'old.wav','status':'ready','duration':2,'summary':{'rms_db':-20}}]}
    upgrade_project(project)
    source = project['tracks'][0]
    assert source['separation_status'] == 'legacy'
    assert source['stems'][0]['file'] == 'old.wav'
    assert source['stems'][0]['id'] == 'old'
    assert source['stems'][0]['summary']['rms_db'] == -20
    assert upgrade_project(project) == project
