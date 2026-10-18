import { api, post, fmt, escapeHtml as esc, icon, preference } from './api.js';
import { StudioPlayer } from './player.js';
import { ComparisonChart, drawWaveform, drawSpectrogram, COLORS } from './charts.js';

const $ = selector => document.querySelector(selector);
const $$ = selector => Array.from(document.querySelectorAll(selector));
const state = JSON.parse($('#studio-data').textContent);
state.analyses = {};
state.chartKind = 'pitch';
state.report = null;
let projectMode = 'new', transformId = '', deleteTarget = null;
let modal = null, previousFocus = null, toastTimer = null;
let refreshing = false, uploading = false, reportJob = null;
let appliedSignature = JSON.stringify(state.project?.tracks || []);
let historySignature = '', rangeInitialized = false;
const player = new StudioPlayer(paintPosition, showError);
const chart = new ComparisonChart($('#analysis-chart'));
player.normalize = preference.get('normalize', true);
player.multi = preference.get('multitrack', false);

function tracks() { return state.project?.tracks || []; }
function ready() { return tracks().some(t => t.status === 'ready'); }
function projectUrl(view = 'workspace') { return `/${view}?project=${encodeURIComponent(state.project.id)}`; }
function showError(message) { $('#error-message').textContent = message; $('#error-banner').hidden = false; }
function notify(message) { clearTimeout(toastTimer); $('#toast span').textContent = message; $('#toast').hidden = false; toastTimer = setTimeout(() => { $('#toast').hidden = true; }, 4500); }
function setPressed(element, value, className = 'on') { element.classList.toggle(className, !!value); element.setAttribute('aria-pressed', String(!!value)); }

function openModal(name) {
  if (modal) modal.hidden = true;
  else previousFocus = document.activeElement;
  modal = $(`[data-modal="${name}"]`);
  modal.hidden = false;
  const input = modal.querySelector('[autofocus]') || modal.querySelector('button,input,select');
  input?.focus();
  syncPreferences();
}
function closeModal() { if (!modal) return; modal.hidden = true; modal = null; previousFocus?.focus(); }
function settingsTab(tab) {
  const button = $(`[data-action="settings-tab"][data-tab="${tab}"]`) || $('[data-action="settings-tab"]');
  tab = button.dataset.tab;
  $$('[data-action="settings-tab"]').forEach(b => b.classList.toggle('on', b === button));
  $$('[data-settings-pane]').forEach(p => { p.hidden = p.dataset.settingsPane !== tab; });
  $('#settings-title').textContent = button.querySelector('strong').textContent;
}
function syncPreferences() {
  const collapsed = preference.get('sidebar-collapsed', false);
  const overview = preference.get('overview', true);
  const dark = document.documentElement.getAttribute('data-ui-theme-mode') !== 'light';
  document.documentElement.classList.toggle('sidebar-collapsed', collapsed);
  $('#overview').hidden = !overview;
  const values = { collapsed, overview, normalize: player.normalize, multi: player.multi, dark };
  $$('[data-preference]').forEach(button => {
    const value = values[button.dataset.preference];
    button.classList.toggle('on', value); button.setAttribute('aria-checked', String(value));
  });
  const style = document.documentElement.getAttribute('data-ui-theme') || 'default';
  $$('[data-action="theme-style"]').forEach(button => setPressed(button, button.dataset.style === style, 'selected'));
  $$('[data-action="normalize"]').forEach(button => setPressed(button, player.normalize));
  $$('[data-action="multi"]').forEach(button => setPressed(button, player.multi));
  $$('[data-multi-label]').forEach(el => { el.textContent = player.multi ? '동시 재생' : 'A/B/C 전환'; });
}
function togglePreference(key) {
  if (key === 'dark') {
    const dark = document.documentElement.getAttribute('data-ui-theme-mode') === 'light';
    if (dark) document.documentElement.removeAttribute('data-ui-theme-mode');
    else document.documentElement.setAttribute('data-ui-theme-mode', 'light');
    document.documentElement.style.colorScheme = dark ? 'dark' : 'light';
    try { localStorage.setItem('mas-theme', dark ? 'dark' : 'light'); } catch { /* optional */ }
    chart.draw();
  } else if (key === 'normalize' || key === 'multi') {
    player[key] = !player[key]; preference.set(key === 'multi' ? 'multitrack' : key, player[key]); player.applyMix();
  } else {
    const storageKey = key === 'collapsed' ? 'sidebar-collapsed' : key;
    preference.set(storageKey, !preference.get(storageKey, key === 'overview'));
  }
  syncPreferences();
}

function paintPosition() {
  const p = player.position;
  if ($('#position-label')) $('#position-label').innerHTML = `${fmt(p)}<span>.${Math.floor(p % 1 * 100).toString().padStart(2, '0')}</span>`;
  if ($('#transport-seek')) $('#transport-seek').value = Math.min(p, player.duration);
  for (const t of tracks()) {
    const host = $(`[data-wave="${t.id}"]`); if (!host) continue;
    const at = Math.max(0, Math.min(t.duration, p + t.offset));
    host.querySelector('input').value = at;
    host.querySelector('.wavehead').style.left = `${t.duration ? at / t.duration * 100 : 0}%`;
  }
  const button = $('[data-action="play"]');
  if (button && button.dataset.playing !== String(player.playing)) {
    button.dataset.playing = String(player.playing); button.setAttribute('aria-label', player.playing ? '일시정지' : '재생'); button.innerHTML = icon(player.playing ? 'pause' : 'play', 18);
  }
}
function syncRange() {
  const duration = player.duration;
  const start = $('#range-start'), end = $('#range-end');
  if (start && end) {
    if (!rangeInitialized || Number(end.value) <= Number(start.value) || Number(end.value) > duration) {
      start.value = 0; end.value = duration; rangeInitialized = duration > 0;
    }
    start.max = Math.max(0, Number(end.value) - .01); end.max = duration;
    player.start = Number(start.value); player.end = Number(end.value);
  } else { player.start = 0; player.end = duration; }
  $$('[data-action="loop"]').forEach(button => setPressed(button, player.loop));
}
function renderControls() {
  if ($('#track-count')) $('#track-count').textContent = `${tracks().length} / 3`;
  $$('[data-requires-ready]').forEach(button => { button.disabled = !ready(); });
  $$('[data-action="upload"]').forEach(button => { button.disabled = uploading || tracks().length >= 3; });
  if ($('#duration-label')) $('#duration-label').textContent = `/ ${fmt(player.duration)}`;
  if ($('#transport-seek')) { $('#transport-seek').max = player.duration || 1; $('#transport-seek').disabled = !ready(); }
  for (const [i, t] of tracks().entries()) {
    const row = $(`[data-track="${t.id}"]`), mix = player.mixFor(t.id);
    row?.classList.toggle('selected', player.active?.id === t.id);
    const select = $(`[data-action="select-track"][data-id="${t.id}"]`); if (select) select.setAttribute('aria-pressed', String(player.active?.id === t.id));
    for (const key of ['mute','solo']) { const button = $(`[data-action="${key}"][data-id="${t.id}"]`); if (button) setPressed(button, mix[key]); }
    const volume = $(`[data-volume="${t.id}"]`); if (volume) volume.value = mix.volume;
    const label = $(`[data-volume-label="${t.id}"]`); if (label) label.textContent = `${Math.round(mix.volume * 100)}%`;
    const listen = $(`[data-action="listen-track"][data-id="${t.id}"]`); if (listen) setPressed(listen, player.active?.id === t.id);
    drawWaveform($(`[data-wave="${t.id}"]`), state.analyses[t.id]);
  }
  if ($('#analysis-track')) $('#analysis-track').value = player.active?.id || '';
  $$('[data-action="generate-report"]').forEach(button => { button.disabled = !ready() || !!reportJob || button.dataset.ai === 'true' && !state.health.ai_configured; });
  syncPreferences(); syncRange(); paintPosition();
}
function renderAnalysis() {
  const analysis = state.analyses[player.active?.id], summary = analysis?.summary;
  $$('[data-metric]').forEach(el => {
    const key = el.dataset.metric;
    el.textContent = !summary ? '—' : key === 'range' ? `${summary.pitch_low_note || '—'} – ${summary.pitch_high_note || '—'}` : summary[key].toFixed(1);
  });
  chart.update(tracks(), state.analyses, state.chartKind, player.start, player.end || player.duration);
  if ($('#analysis-limit')) $('#analysis-limit').hidden = !analysis;
  if ($('#analysis-warnings')) $('#analysis-warnings').replaceChildren(...(analysis?.warnings || []).map(text => { const p = document.createElement('p'); p.textContent = text; return p; }));
  drawSpectrogram($('#spectrogram canvas'), analysis);
  if ($('#spectro-duration')) $('#spectro-duration').textContent = fmt(summary?.duration || 0);
  if ($('#vibrato-count')) $('#vibrato-count').textContent = analysis ? `${analysis.vibrato_candidates.length}개` : '—';
  if ($('#vibrato-items')) {
    $('#vibrato-items').innerHTML = analysis?.vibrato_candidates.length ? analysis.vibrato_candidates.slice(0,8).map((v,i) => `<button class="vibrato-item" data-action="vibrato" data-index="${i}"><span>${v.start.toFixed(1)}–${v.end.toFixed(1)}초 <small>원본 기준</small></span><span>${v.rate_hz} Hz · 폭 ${v.width_cents} cent</span>${icon('play',12)}</button>`).join('') : '<p class="muted tiny">기준에 맞는 주기적 변화가 발견되지 않았습니다. 창법에 대한 판단은 아닙니다.</p>';
  }
}
function rebuildTrackChoices() {
  const select = $('#analysis-track');
  if (select) select.innerHTML = tracks().map((t,i) => `<option value="${t.id}">${'ABC'[i]} · ${esc(t.name)}</option>`).join('') || '<option value="">선택할 트랙 없음</option>';
  if ($('#ab-switch')) $('#ab-switch').innerHTML = tracks().map((t,i) => `<button data-action="listen-track" data-id="${t.id}" style="--track-color:${COLORS[i]}" ${t.status !== 'ready' ? 'disabled' : ''}>${'ABC'[i]}</button>`).join('');
  if ($('#chart-legend')) $('#chart-legend').innerHTML = tracks().map((t,i) => `<span><i style="background:${COLORS[i]}"></i>${'ABC'[i]} <span class="legend-name">${esc(t.name)}</span></span>`).join('');
}
function renderHistory() {
  const select = $('#report-history'), reports = state.project?.reports || [];
  const signature = JSON.stringify(reports.map(r => r.created_at));
  if (!select || signature === historySignature) return;
  historySignature = signature;
  select.innerHTML = `<option value="">이전 리포트 열기 (${reports.length})</option>` + reports.map((r,i) => `<option value="${i}">${r.kind === 'ai' ? 'AI 평가' : '측정 리포트'} · ${esc(new Date(r.created_at).toLocaleString('ko-KR'))}</option>`).join('');
}
async function loadAnalyses() {
  const needed = tracks().filter(t => t.status === 'ready' && !state.analyses[t.id]);
  const data = await Promise.all(needed.map(async t => [t.id, await api(`/projects/${state.project.id}/tracks/${t.id}/analysis`)]));
  Object.assign(state.analyses, Object.fromEntries(data));
  if (data.length) { renderControls(); renderAnalysis(); }
}
async function refresh() {
  if (refreshing) return; refreshing = true;
  try {
    const projects = await api('/projects');
    $('#connection-label').textContent = 'Local studio';
    const all = projects.flatMap(p => p.tracks);
    [projects.length,all.length,all.filter(t => t.status === 'ready').length,all.filter(t => ['queued','processing'].includes(t.status)).length].forEach((value,i) => { $(`[data-overview="${i}"]`).textContent = value; });
    if (!state.project) return;
    const project = projects.find(p => p.id === state.project.id);
    if (!project) { player.pause(); showError('프로젝트가 삭제되었습니다. 대시보드에서 다른 프로젝트를 선택하세요.'); return; }
    state.project = project;
    const signature = JSON.stringify(project.tracks);
    if (signature !== appliedSignature) {
      const container = $('#tracks-container');
      const editingTrack = container?.contains(document.activeElement) && document.activeElement.matches('input,select,textarea');
      if (container && !editingTrack) {
        const response = await fetch(`/ui/tracks/${project.id}`);
        if (!response.ok) throw new Error('트랙 화면을 갱신할 수 없습니다.');
        container.innerHTML = await response.text();
        appliedSignature = signature;
        rebuildTrackChoices();
      } else if (!container) appliedSignature = signature;
      player.setTracks(project.id, project.tracks);
      renderControls(); renderAnalysis();
    }
    const jobs = await Promise.all(tracks().filter(t => ['queued','processing'].includes(t.status) && t.job_id).map(async t => [t.id, await api(`/jobs/${t.job_id}`)]));
    for (const [id, job] of jobs) {
      const message = $(`[data-job-message="${id}"]`); if (message) message.textContent = job.message;
      const bar = $(`[data-progress-bar="${id}"]`); if (bar) bar.style.width = `${job.progress}%`;
      const label = $(`[data-progress-label="${id}"]`); if (label) label.textContent = `${job.progress}%`;
    }
    renderHistory(); await loadAnalyses();
  } catch (error) { $('#connection-label').textContent = '연결 확인 필요'; showError(error.message); }
  finally { refreshing = false; }
}

async function uploadFiles(files) {
  if (!files?.length || uploading) return;
  let target = state.project, failure = null;
  const list = Array.from(files);
  if (list.length + tracks().length > 3) { showError('프로젝트당 최대 3개입니다. 빈 슬롯 수에 맞게 파일을 선택하세요.'); return; }
  if (list.some(f => !/\.(wav|mp3|flac)$/i.test(f.name))) { showError('WAV, MP3, FLAC 파일만 지원합니다.'); return; }
  if (list.some(f => f.size > state.health.max_upload_mb * 1024 * 1024)) { showError(`파일당 최대 ${state.health.max_upload_mb}MB까지 업로드할 수 있습니다.`); return; }
  uploading = true; renderControls(); notify('음원을 업로드하고 있습니다.');
  try {
    if (!target) target = await post('/projects', { name: '새 보컬 프로젝트' });
    for (const file of list) { const body = new FormData(); body.append('file', file); await api(`/projects/${target.id}/tracks`, { method: 'POST', body }); }
  } catch (error) { failure = error.message; }
  finally { uploading = false; $('#audio-upload').value = ''; }
  if (target) {
    if (failure) sessionStorage.setItem('mas-page-error', failure);
    window.location.assign(`/workspace?project=${target.id}`);
  } else { renderControls(); showError(failure || '프로젝트를 만들 수 없습니다.'); }
}
function showReport(report) {
  state.report = report;
  if (!$('#report-output')) return;
  $('#report-output').innerHTML = `<div class="report-content"><div class="report-meta"><span class="small-badge">${esc(report.kind === 'ai' ? `AI 해석 · ${report.model}` : '측정 기반 · AI 미사용')}</span><span>${esc(new Date(report.created_at).toLocaleString('ko-KR'))}</span></div><pre class="report-text"></pre><details><summary>분석 근거 JSON</summary><pre class="report-evidence"></pre></details></div>`;
  $('.report-text').textContent = report.content;
  $('.report-evidence').textContent = JSON.stringify(report.evidence, null, 2);
  $('#export-report').hidden = false;
}
async function followReport(id) {
  reportJob = id; renderControls();
  try {
    const job = await api(`/jobs/${id}`);
    if (job.status === 'failed') throw new Error(job.message);
    if (job.status === 'done') { reportJob = null; showReport(job.result); notify('리포트가 완성되었습니다.'); renderControls(); void refresh(); return; }
    $('#report-output').innerHTML = `<div class="report-wait">${icon('loader-circle',30,'spin')}<h3>${esc(job.message)}</h3><p>측정 데이터를 해석하고 있습니다.</p></div>`;
    setTimeout(() => void followReport(id), 1200);
  } catch (error) { reportJob = null; showError(error.message); renderControls(); $('#report-output').textContent = error.message; }
}
async function makeReport(ai) {
  syncRange();
  if (!(player.end > player.start)) throw new Error('구간 끝은 시작보다 커야 합니다.');
  const job = await post(`/projects/${state.project.id}/reports`, { start: player.start, end: Math.min(player.end, player.duration), ai });
  if (state.page !== 'reports') {
    window.location.assign(`${projectUrl('reports')}&job=${job.id}&start=${player.start}&end=${player.end}`);
  } else { $('#export-report').hidden = true; await followReport(job.id); }
}

document.addEventListener('click', async event => {
  const button = event.target.closest('[data-action]'); if (!button || button.disabled) return;
  const action = button.dataset.action, id = button.dataset.id;
  try {
    switch (action) {
      case 'new-project': projectMode = 'new'; $('#project-modal-title').textContent = '새 보컬 프로젝트'; $('#project-form input').value = ''; openModal('project'); break;
      case 'rename-project': projectMode = 'rename'; $('#project-modal-title').textContent = '프로젝트 이름 변경'; $('#project-form input').value = state.project.name; openModal('project'); break;
      case 'close-modal': closeModal(); break;
      case 'theme': openModal('theme'); break;
      case 'help': openModal('help'); break;
      case 'settings': settingsTab(button.dataset.tab || 'display'); openModal('settings'); break;
      case 'settings-tab': settingsTab(button.dataset.tab); break;
      case 'theme-style': {
        const style = button.dataset.style;
        if (style === 'default') document.documentElement.removeAttribute('data-ui-theme'); else document.documentElement.setAttribute('data-ui-theme', style);
        try { localStorage.setItem('mas-theme-style', style); } catch { /* optional */ }
        syncPreferences(); chart.draw(); break;
      }
      case 'open-nav': $('#sidebar').classList.add('mobile-open'); $('#sidebar-overlay').hidden = false; break;
      case 'close-nav': $('#sidebar').classList.remove('mobile-open'); $('#sidebar-overlay').hidden = true; break;
      case 'collapse': togglePreference('collapsed'); break;
      case 'normalize': togglePreference('normalize'); break;
      case 'multi': togglePreference('multi'); break;
      case 'dismiss-error': $('#error-banner').hidden = true; break;
      case 'upload': $('#audio-upload').click(); break;
      case 'play': player.playing ? player.pause() : player.play(); break;
      case 'stop': player.stop(); break;
      case 'loop': player.loop = !player.loop; syncRange(); break;
      case 'select-track': case 'listen-track':
        player.selected = id; if (action === 'listen-track') { player.multi = false; preference.set('multitrack', false); }
        player.applyMix(); renderControls(); renderAnalysis(); break;
      case 'mute': case 'solo': player.mixFor(id)[action] = !player.mixFor(id)[action]; player.applyMix(); renderControls(); break;
      case 'chart-kind':
        state.chartKind = button.dataset.kind;
        $$('[data-action="chart-kind"]').forEach(b => b.classList.toggle('on', b === button));
        $('#chart-title').textContent = { pitch: '음정 윤곽', rms: '다이내믹', centroid: '스펙트럼 중심' }[state.chartKind];
        $('#chart-method').textContent = { pitch: 'pYIN', rms: 'RMS', centroid: 'STFT' }[state.chartKind]; renderAnalysis(); break;
      case 'transform':
        transformId = id; $('#transform-form').reset(); $('#semitones-label').textContent = '0 반음'; $('#transform-speed-label').textContent = '1.00×'; openModal('transform'); break;
      case 'retry': await post(`/projects/${state.project.id}/tracks/${id}/analyze`); await refresh(); break;
      case 'delete-project': case 'delete-track':
        deleteTarget = { type: action === 'delete-project' ? 'project' : 'track', id };
        $('#delete-description').textContent = `“${button.dataset.name}”의 오디오와 분석 데이터를 삭제합니다. 복구할 수 없습니다.`; openModal('delete'); break;
      case 'confirm-delete': {
        button.disabled = true;
        try {
          const target = deleteTarget;
          await api(target.type === 'project' ? `/projects/${target.id}` : `/projects/${state.project.id}/tracks/${target.id}`, { method: 'DELETE' });
          if (target.type === 'project') { window.location.assign('/dashboard'); return; }
          closeModal(); rangeInitialized = false; await refresh(); notify('파일과 분석 데이터를 삭제했습니다.');
        } finally { button.disabled = false; }
        break;
      }
      case 'generate-report':
        button.disabled = true;
        try { await makeReport(button.dataset.ai === 'true'); } finally { renderControls(); }
        break;
      case 'export-report': {
        if (!state.report) break;
        const url = URL.createObjectURL(new Blob([JSON.stringify(state.report, null, 2)], { type: 'application/json' }));
        const link = document.createElement('a'); link.href = url; link.download = 'mas-report.json'; link.click(); setTimeout(() => URL.revokeObjectURL(url), 1000); break;
      }
      case 'vibrato': {
        const candidate = state.analyses[player.active.id].vibrato_candidates[Number(button.dataset.index)];
        const start = Math.max(0, candidate.start - player.active.offset), end = Math.min(player.duration, candidate.end - player.active.offset);
        if (end <= start) throw new Error('이 후보는 오프셋 적용 후 재생 구간 밖에 있습니다.');
        $('#range-start').value = start; $('#range-end').value = end; player.loop = true; syncRange(); player.seek(start); renderAnalysis(); break;
      }
    }
  } catch (error) { showError(error.message); }
});
document.addEventListener('click', event => {
  const toggle = event.target.closest('[data-preference]'); if (toggle) togglePreference(toggle.dataset.preference);
  if (event.target === modal) closeModal();
});
document.addEventListener('keydown', event => {
  if (!modal) return;
  if (event.key === 'Escape') { closeModal(); return; }
  if (event.key !== 'Tab') return;
  const nodes = Array.from(modal.querySelectorAll('button:not(:disabled),input,select,summary,a[href]')).filter(el => el.getClientRects().length);
  const first = nodes[0], last = nodes[nodes.length - 1];
  if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last?.focus(); }
  if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first?.focus(); }
});
$('#project-form').addEventListener('submit', async event => {
  event.preventDefault(); const button = event.submitter; button.disabled = true;
  try {
    const name = $('#project-form input').value.trim() || '새 보컬 프로젝트';
    const project = projectMode === 'new' ? await post('/projects', { name }) : await api(`/projects/${state.project.id}`, { method: 'PATCH', body: JSON.stringify({ name }) });
    window.location.assign(`/workspace?project=${project.id}`);
  } catch (error) { showError(error.message); } finally { button.disabled = false; }
});
$('#transform-form').addEventListener('submit', async event => {
  event.preventDefault(); const button = event.submitter; button.disabled = true;
  try {
    const form = new FormData(event.currentTarget);
    await post(`/projects/${state.project.id}/tracks/${transformId}/transform`, { semitones: Number(form.get('semitones')), speed: Number(form.get('speed')), normalize: form.has('normalize') });
    closeModal(); await refresh(); notify('변형본을 만들고 다시 분석합니다.');
  } catch (error) { showError(error.message); } finally { button.disabled = false; }
});
$('#audio-upload').addEventListener('change', event => void uploadFiles(event.target.files));
$('#upload-zone')?.addEventListener('dragover', event => { event.preventDefault(); $('#upload-zone').classList.add('dragging'); });
$('#upload-zone')?.addEventListener('dragleave', () => $('#upload-zone').classList.remove('dragging'));
$('#upload-zone')?.addEventListener('drop', event => { event.preventDefault(); $('#upload-zone').classList.remove('dragging'); void uploadFiles(event.dataTransfer.files); });
document.addEventListener('input', event => {
  const el = event.target;
  if (el.dataset.volume) { player.mixFor(el.dataset.volume).volume = Number(el.value); player.applyMix(); const label = $(`[data-volume-label="${el.dataset.volume}"]`); if (label) label.textContent = `${Math.round(Number(el.value)*100)}%`; }
  if (el.dataset.waveSeek) { const t = tracks().find(t => t.id === el.dataset.waveSeek); player.seek(Number(el.value) - t.offset); }
  if (el.id === 'transport-seek') player.seek(Number(el.value));
  if (el.id === 'project-search') $$('[data-project-name]').forEach(card => { card.hidden = !card.dataset.projectName.toLocaleLowerCase().includes(el.value.toLocaleLowerCase()); });
  if (el.name === 'semitones') $('#semitones-label').textContent = `${Number(el.value)>0?'+':''}${el.value} 반음`;
  if (el.name === 'speed') $('#transform-speed-label').textContent = `${Number(el.value).toFixed(2)}×`;
});
document.addEventListener('change', async event => {
  const el = event.target;
  try {
    if (el.dataset.offset) {
      await api(`/projects/${state.project.id}/tracks/${el.dataset.offset}`, { method: 'PATCH', body: JSON.stringify({ offset: Number(el.value) }) });
      rangeInitialized = false; await refresh();
    }
    if (el.id === 'playback-speed') player.setSpeed(Number(el.value));
    if (el.id === 'analysis-track') { player.selected = el.value; player.applyMix(); renderControls(); renderAnalysis(); }
    if (el.id === 'range-start' || el.id === 'range-end') {
      const start = Math.max(0, Number($('#range-start').value)), end = Math.min(player.duration, Number($('#range-end').value));
      if (!Number.isFinite(start) || !Number.isFinite(end) || end <= start) { showError('구간 끝은 시작보다 커야 합니다.'); return; }
      $('#range-start').value = start; $('#range-end').value = end; syncRange(); renderAnalysis();
    }
    if (el.id === 'report-history' && el.value !== '') showReport(state.project.reports[Number(el.value)]);
  } catch (error) { showError(error.message); }
});
window.addEventListener('pagehide', () => player.destroy());
window.addEventListener('pageshow', event => { if (event.persisted) { player.setTracks(state.project?.id, tracks()); renderControls(); } });

player.setTracks(state.project?.id, tracks());
renderControls(); renderAnalysis(); renderHistory();
const query = new URLSearchParams(window.location.search);
if (state.page === 'reports') {
  const start = Number(query.get('start')), end = Number(query.get('end'));
  if (end > start) { $('#range-start').value = Math.max(0, start); $('#range-end').value = Math.min(end, player.duration); syncRange(); }
  if (query.get('job')) void followReport(query.get('job'));
}
const pageError = sessionStorage.getItem('mas-page-error');
if (pageError) { showError(pageError); sessionStorage.removeItem('mas-page-error'); }
void loadAnalyses().catch(error => showError(error.message));
void refresh();
setInterval(() => void refresh(), 2000);
