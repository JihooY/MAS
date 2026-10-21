import { t, locale } from './i18n.js';
import { api, post, fmt, escapeHtml as esc, icon, preference } from './api.js';
import { StudioPlayer } from './player.js';
import { ComparisonChart, drawWaveform, drawSpectrogram, COLORS } from './charts.js';
const $ = selector => document.querySelector(selector);
const $$ = selector => Array.from(document.querySelectorAll(selector));
const state = JSON.parse($('#studio-data').textContent);
state.analyses = {}; state.chartKind = 'pitch'; state.report = null;
state.vocalId = ''; state.mode = preference.get('analysis-mode','normal');
state.enabled = preference.get(`analysis-${state.source_id}`,false);
let modal = null, previousFocus = null, projectMode = 'new', deleteTarget = null;
let refreshing = false, uploading = false, analysisSubmitting = false, reportSubmitting = false;
let signature = '', historySignature = '', rangeInitialized = false, toastTimer;
let queueSignature = '', displayedReportJob = '';
state.report_queue ||= {busy:false,jobs:[],recent:[]};
if(state.report_queue.jobs.some(j=>j.project_id===state.project?.id&&j.source_ids.includes(state.source_id)))state.enabled=true;
const player = new StudioPlayer(paintPosition, showError);
player.normalize = false;
const chart = new ComparisonChart($('#analysis-chart'));
const sources = () => state.project?.tracks || [];
const source = () => sources().find(s => s.id === state.source_id) || sources()[0];
const vocals = () => source()?.stems?.filter(v=>v.kind==='vocals') || [];
const vocal = () => vocals().find(v=>v.id===state.vocalId) || vocals()[0];
const busy = () => state.report_queue.jobs.some(j=>j.project_id===state.project?.id) || sources().some(s=>['queued','processing'].includes(s.separation_status) || s.stems?.some(v=>['queued','processing'].includes(v.status)));
function playbackTracks() {
  const s=source(); if(!s)return [];
  const root=`/api/projects/${state.project.id}/tracks/${s.id}`;
  const tracks=[];
  if(s.status==='ready')tracks.push({...s,id:s.id+'-original',name:t('원본 음악'),kind:'original',status:'ready',offset:0,url:root+'/audio'});
  for(const v of [...(s.stems||[])].sort((a,b)=>(a.kind!=='vocals')-(b.kind!=='vocals')||Number(!!a.derived)-Number(!!b.derived)))
    if(v.duration)tracks.push({...v,name:t(v.name),status:'ready',offset:0,url:`${root}/stems/${v.id}/audio`});
  return tracks;
}
function showError(message){$('#error-message').textContent=t(message);$('#error-banner').hidden=false;}
function notify(message){clearTimeout(toastTimer);$('#toast span').textContent=message;$('#toast').hidden=false;toastTimer=setTimeout(()=>$('#toast').hidden=true,4500);}
function pressed(el,on,cls='on'){el.classList.toggle(cls,!!on);el.setAttribute('aria-pressed',String(!!on));}
function openModal(name){if(modal)modal.hidden=true;else previousFocus=document.activeElement;modal=$(`[data-modal="${name}"]`);modal.hidden=false;(modal.querySelector('[autofocus]')||modal.querySelector('button,input,select'))?.focus();syncPreferences();}
function closeModal(){if(modal){modal.hidden=true;modal=null;previousFocus?.focus();}}
function settingsTab(tab){const button=$(`[data-action="settings-tab"][data-tab="${tab}"]`)||$('[data-action="settings-tab"]');$$('[data-action="settings-tab"]').forEach(b=>b.classList.toggle('on',b===button));$$('[data-settings-pane]').forEach(p=>p.hidden=p.dataset.settingsPane!==button.dataset.tab);$('#settings-title').textContent=button.querySelector('strong').textContent;}
function syncPreferences(){
  document.documentElement.classList.toggle('sidebar-collapsed',preference.get('sidebar-collapsed',false));$('#overview').hidden=!preference.get('overview',true);
  const dark=document.documentElement.getAttribute('data-ui-theme-mode')!=='light';
  const values={dark,collapsed:preference.get('sidebar-collapsed',false),overview:preference.get('overview',true)};
  $$('[data-preference]').forEach(b=>{b.classList.toggle('on',!!values[b.dataset.preference]);b.setAttribute('aria-checked',String(!!values[b.dataset.preference]));});
  const style=document.documentElement.getAttribute('data-ui-theme')||'default';$$('[data-action="theme-style"]').forEach(b=>pressed(b,b.dataset.style===style,'selected'));
}
function togglePreference(key){
  if(key==='dark'){const dark=document.documentElement.getAttribute('data-ui-theme-mode')==='light';if(dark)document.documentElement.removeAttribute('data-ui-theme-mode');else document.documentElement.setAttribute('data-ui-theme-mode','light');document.documentElement.style.colorScheme=dark?'dark':'light';try{localStorage.setItem('mas-theme',dark?'dark':'light');}catch{}chart.draw();}
  else {const storageKey=key==='collapsed'?'sidebar-collapsed':key;preference.set(storageKey,!preference.get(storageKey,key==='overview'));}
  syncPreferences();
}
function paintPosition(){
  if($('#position-label'))$('#position-label').innerHTML=`${fmt(player.position)}<span>.${Math.floor(player.position%1*100).toString().padStart(2,'0')}</span>`;
  if($('#transport-seek'))$('#transport-seek').value=player.position;
  for(const t of player.tracks){const host=$(`[data-wave="${t.id}"]`);if(!host)continue;const at=Math.max(0,Math.min(t.duration,player.position));host.querySelector('input').value=at;host.querySelector('.wavehead').style.left=`${t.duration?at/t.duration*100:0}%`;}
  const b=$('[data-action="play"]');if(b&&b.dataset.playing!==String(player.playing)){b.dataset.playing=String(player.playing);b.setAttribute('aria-label',player.playing?t('일시정지'):t('재생'));b.innerHTML=icon(player.playing?'pause':'play',18);}
}
function syncRange(){if(!$('#range-start'))return;const start=$('#range-start'),end=$('#range-end'),duration=source()?.duration||0;if(!rangeInitialized||Number(end.value)<=Number(start.value)||Number(end.value)>duration){start.value=0;end.value=duration;rangeInitialized=duration>0;}start.max=Math.max(0,Number(end.value)-.01);end.max=duration;player.start=Number(start.value);player.end=Number(end.value);$$('[data-action="loop"]').forEach(b=>pressed(b,player.loop));}
function renderControls(){
  const s=source(),v=vocal();
  for(const item of sources()){const label=$(`[data-source-meta="${item.id}"]`);if(label)label.textContent=`${fmt(item.duration||0)} · ${{ready:t('분리 완료'),processing:t('분리 중'),queued:t('대기 중'),failed:t('분리 확인 필요'),legacy:t('이전 보컬 파일')}[item.separation_status]||t('준비 중')}`;}
  $$('[data-action="upload"]').forEach(b=>b.disabled=uploading||sources().length>=3);
  if(!s){syncPreferences();return;}
  if($('#stem-count'))$('#stem-count').textContent=s.stems?.length||0;
  if($('#source-duration'))$('#source-duration').textContent=fmt(s.duration||0);
  if($('#separation-status'))$('#separation-status').textContent=s.separation_model||t('이전 보컬');
  const notice=$('#separation-notice');if(notice){notice.hidden=s.separation_status==='ready';$('#source-job-message').textContent=t(s.error)||(s.separation_status==='legacy'?t('이전 보컬 파일입니다. 원본 음악을 넣으면 악기별 분리가 가능합니다.'):t('보컬 · 악기 트랙 분리 중입니다.'));}
  $$('[data-action="separate"],[data-action="delete-source"]').forEach(b=>b.disabled=busy());
  if($('#transport-seek')){$('#transport-seek').max=player.duration||1;$('#transport-seek').disabled=!player.duration;$('#duration-label').textContent='/ '+fmt(player.duration);$('[data-action="play"]').disabled=!player.duration;}
  for(const t of player.tracks){const mix=player.mixFor(t.id);for(const key of ['mute','solo']){const b=$(`[data-action="${key}"][data-id="${t.id}"]`);if(b)pressed(b,mix[key]);}const input=$(`[data-volume="${t.id}"]`);if(input)input.value=mix.volume;const label=$(`[data-volume-label="${t.id}"]`);if(label)label.textContent=Math.round(mix.volume*100)+'%';drawWaveform($(`[data-wave="${t.id}"]`),t.waveform?{waveform:t.waveform}:state.analyses[t.id]);}
  const monitor=$('[data-action="monitor-original"]');if(monitor)pressed(monitor,player.monitorOriginal||!player.tracks.some(t=>t.kind!=='original'));
  const select=$('#analysis-track');if(select){const old=select.value;select.innerHTML=vocals().map(v=>`<option value="${v.id}">${esc(t(v.name))}</option>`).join('');select.value=v?.id||old;}
  if($('#analysis-toggle')){const toggle=$('#analysis-toggle');toggle.classList.toggle('on',state.enabled);toggle.setAttribute('aria-checked',String(state.enabled));toggle.setAttribute('aria-label',state.enabled?t('보컬 분석 끄기'):t('보컬 분석 켜기'));$('#analysis-disabled').hidden=state.enabled;$('#analysis-enabled').hidden=!state.enabled;$('#analysis-availability').textContent=v?t('보컬 분리 완료 · 분석 가능'):t('먼저 보컬 분리를 완료하세요');}
  $$('[data-action="analysis-mode"]').forEach(b=>pressed(b,b.dataset.mode===state.mode));$$('[data-advanced]').forEach(el=>el.hidden=state.mode!=='advanced');
  if($('#analysis-state'))$('#analysis-state').textContent=v?({pending:t('분석을 켜면 측정을 시작합니다'),queued:t('보컬 분석 대기'),processing:t('보컬 분석 중'),ready:t('실제 측정 완료'),failed:t(v.error)||t('분석 실패')}[v.status]||''):t('보컬 분리 필요');
  $$('[data-action="analyze"]').forEach(b=>b.disabled=!v||['queued','processing'].includes(v.status)||analysisSubmitting);
  $$('[data-action="transform"]').forEach(b=>b.disabled=v?.status!=='ready');
  $$('[data-action="generate-report"]').forEach(b=>{
    const ai=b.dataset.ai==='true',queued=b.id==='enqueue-ai';
    b.disabled=v?.status!=='ready'||reportSubmitting||ai&&!state.health.ai_configured||ai&&!queued&&state.report_queue.busy;
    if(queued)b.hidden=!state.report_queue.busy;
    if(!ai){b.dataset.enqueue=String(state.report_queue.busy);b.textContent=state.report_queue.busy?t('측정 대기열에 추가'):t('측정 리포트');}
  });
  syncPreferences();syncRange();paintPosition();
}
function graphVocals(){if($('#compare-vocals')?.checked)return sources().map((s,i)=>{const v=s.stems?.find(v=>v.kind==='vocals'&&!v.derived);return v?{...v,name:s.name,offset:s.offset||0,color:COLORS[i]}:null;}).filter(Boolean);const v=vocal();return v?[{...v,name:source().name+' · '+t(v.name),offset:0}]:[];}
function renderAnalysis(){
  const a=state.analyses[vocal()?.id],s=a?.summary;
  $$('[data-metric]').forEach(el=>el.textContent=!s?'—':el.dataset.metric==='range'?`${s.pitch_low_note||'—'} – ${s.pitch_high_note||'—'}`:s[el.dataset.metric].toFixed(1));
  const vocals=graphVocals();if($('#chart-legend'))$('#chart-legend').innerHTML=vocals.map((v,i)=>`<span><i style="background:${COLORS[i]}"></i><span class="legend-name">${esc(v.name)}</span></span>`).join('');
  chart.update(vocals,state.analyses,state.chartKind,player.start,player.end||source()?.duration||0);
  drawSpectrogram($('#spectrogram canvas'),a);if($('#spectro-duration'))$('#spectro-duration').textContent=fmt(s?.duration||0);
  if($('#analysis-limit')){$('#analysis-limit').hidden=!a;$('#analysis-warnings').replaceChildren(...(a?.warnings||[]).map(text=>{const p=document.createElement('p');p.textContent=t(text);return p;}));}
  if($('#vibrato-count'))$('#vibrato-count').textContent=a?t('{count}개',{count:a.vibrato_candidates.length}):'—';
  if($('#vibrato-items'))$('#vibrato-items').innerHTML=a?.vibrato_candidates.length?a.vibrato_candidates.slice(0,8).map((v,i)=>`<button class="vibrato-item" data-action="vibrato" data-index="${i}"><span>${esc(t('{start}–{end}초',{start:v.start.toFixed(1),end:v.end.toFixed(1)}))}</span><span>${v.rate_hz} Hz · ${v.width_cents} cent</span>${icon('play',12)}</button>`).join(''):`<p class="muted tiny">${esc(t('기준에 맞는 비브라토 후보가 없습니다. 창법 판정은 아닙니다.'))}</p>`;
}
async function loadAnalyses(){if(!state.enabled)return;const needed=sources().flatMap(s=>(s.stems||[]).filter(v=>v.kind==='vocals'&&v.status==='ready'&&!state.analyses[v.id]).map(v=>({s,v})));for(const {s,v}of needed)state.analyses[v.id]=await api(`/projects/${state.project.id}/tracks/${s.id}/stems/${v.id}/analysis`);renderAnalysis();}
async function analyze(force=false){const v=vocal();if(!v||analysisSubmitting||['queued','processing'].includes(v.status)||(!force&&v.status!=='pending'))return;analysisSubmitting=true;try{await post(`/projects/${state.project.id}/tracks/${source().id}/analyze`,{vocal_id:v.id});v.status='queued';renderControls();}finally{analysisSubmitting=false;}}
function renderHistory(){const reports=state.project?.reports||[],key=JSON.stringify(reports.map(r=>r.created_at));if(!$('#report-history')||key===historySignature)return;historySignature=key;$('#report-history').innerHTML=`<option value="">${esc(t('이전 리포트 열기 ({count})',{count:reports.length}))}</option>`+reports.map((r,i)=>`<option value="${i}">${r.kind==='ai'?t('AI 평가'):t('측정 리포트')} · ${esc(new Date(r.created_at).toLocaleString(locale))}</option>`).join('');}
async function refresh(){
  if(refreshing)return;refreshing=true;
  try{const [projects,queue]=await Promise.all([api('/projects'),api('/report-jobs')]);state.report_queue=queue;await syncReportQueue();$('#connection-label').textContent='Local studio';const all=projects.flatMap(p=>p.tracks);[projects.length,all.length,all.filter(s=>s.separation_status==='ready').length,all.filter(s=>['queued','processing'].includes(s.separation_status)).length].forEach((n,i)=>{$(`[data-overview="${i}"]`).textContent=n;});if(!state.project||state.page==='dashboard')return;const current=projects.find(p=>p.id===state.project.id);if(!current){showError(t('프로젝트가 삭제되었습니다.'));return;}state.project=current;
    if(!source())return;const next=JSON.stringify(source());if(next!==signature){const container=$('#tracks-container');const editing=container?.contains(document.activeElement)&&document.activeElement.matches('input,select,textarea');if(container&&!editing){const res=await fetch(`/ui/tracks/${state.project.id}?source=${source().id}`);if(!res.ok)throw new Error(t('분리 트랙 화면을 갱신할 수 없습니다.'));container.innerHTML=await res.text();signature=next;}player.setTracks(state.project.id,playbackTracks());renderControls();renderAnalysis();}
    renderControls();const s=source();if(s.job_id&&['queued','processing'].includes(s.separation_status)){const j=await api(`/jobs/${s.job_id}`);$('#source-job-message').textContent=t(j.message);$('#source-job-progress').textContent=j.progress+'%';}
    const v=vocal();if(v?.job_id&&['queued','processing'].includes(v.status)){const j=await api(`/jobs/${v.job_id}`);$('#analysis-state').textContent=`${t(j.message)} · ${j.progress}%`;}
    renderHistory();await loadAnalyses();if(state.enabled&&vocal()?.status==='pending')await analyze();
  }catch(e){$('#connection-label').textContent=t('연결 확인 필요');showError(e.message);}finally{refreshing=false;}
}
async function uploadFiles(files){if(!files?.length||uploading)return;const list=Array.from(files);if(list.length+sources().length>3){showError(t('프로젝트에는 원본 음악을 최대 3개까지 넣을 수 있습니다.'));return;}if(list.some(f=>!/[.](wav|mp3|flac)$/i.test(f.name))){showError(t('WAV, MP3, FLAC 파일만 지원합니다.'));return;}if(list.some(f=>f.size>state.health.max_upload_mb*1024*1024)){showError(t('파일당 최대 {size}MB입니다.',{size:state.health.max_upload_mb}));return;}uploading=true;renderControls();let target=state.project,lastId,failure;try{if(!target)target=await post('/projects',{name:t('새 음악 프로젝트')});for(const file of list){const body=new FormData();body.append('file',file);const result=await api(`/projects/${target.id}/tracks`,{method:'POST',body});lastId=result.track.id;}}catch(e){failure=e.message;}finally{uploading=false;$('#audio-upload').value='';}if(target){if(failure)sessionStorage.setItem('mas-page-error',failure);location.assign(`/workspace?project=${target.id}${lastId?'&source='+lastId:''}`);}else{showError(failure);renderControls();}}
function showReport(report){state.report=report;$('#report-output').classList.remove('report-placeholder');$('#report-output').innerHTML=`<div class="report-content"><div class="report-meta"><span class="small-badge">${esc(report.kind==='ai'?t('AI 분석 · ')+report.model:t('측정 기반 · AI 미사용'))}</span><span>${esc(new Date(report.created_at).toLocaleString(locale))}</span></div><article class="markdown-body"></article><details><summary>${esc(t('측정 근거 JSON'))}</summary><pre class="report-evidence"></pre></details></div>`;if(report.content_html)$('.markdown-body').innerHTML=report.content_html;else $('.markdown-body').textContent=report.content;$('.report-evidence').textContent=JSON.stringify(report.evidence,null,2);$('#export-report').hidden=false;}
async function syncReportQueue(){
  const queue=state.report_queue,panel=$('#report-queue-panel'),key=JSON.stringify(queue.jobs);
  panel.hidden=!queue.busy;
  if(queueSignature!==key){queueSignature=key;$('#report-queue-count').textContent=t('실행 중 {running} · 대기 {queued}',{running:queue.jobs.filter(j=>j.status==='processing').length,queued:queue.jobs.filter(j=>j.status==='queued').length});
    $('#report-queue-list').innerHTML=queue.jobs.map(j=>`<li class="${j.status==='processing'?'is-running':''}"><span class="queue-state">${j.status==='processing'?t('진행 중'):t('대기 ')+j.queue_position}</span><a href="/workspace?project=${encodeURIComponent(j.project_id)}${j.source_ids[0]?'&source='+encodeURIComponent(j.source_ids[0]):''}">${esc(j.project_name)}</a><span>${j.kind==='ai'?t('AI 분석'):t('측정 리포트')} · ${esc(t(j.message))}</span></li>`).join('');
  }
  if(!$('#report-output'))return;
  const matching=[...queue.recent,...queue.jobs].filter(j=>j.project_id===state.project?.id&&j.source_ids.includes(state.source_id)).sort((a,b)=>b.created_at.localeCompare(a.created_at));
  const job=matching[0];if(!job)return;
  const stamp=job.id+job.status+job.message+job.queue_position;
  if(displayedReportJob===stamp)return;
  if(['queued','processing'].includes(job.status)){
    $('#export-report').hidden=true;
    $('#report-output').innerHTML=`<div class="report-wait">${icon('loader-circle',26,'spin')}<h3>${esc(job.status==='queued'?t('대기열 {position}번째 · 순서대로 처리합니다',{position:job.queue_position}):t(job.message))}</h3><p class="tiny muted">${esc(job.kind==='ai'?t('AI 분석'):t('측정 리포트'))} · ${esc(t('창을 닫아도 서버에서 계속 처리합니다.'))}</p></div>`;
  }else if(job.status==='done'){
    const completed=await api(`/jobs/${job.id}`);showReport(completed.result);
  }else $('#report-output').textContent=t(job.message);
  displayedReportJob=stamp;
}
async function makeReport(ai,enqueue){
  if(reportSubmitting)return;reportSubmitting=true;renderControls();
  try{const compare=$('#report-scope').value==='project';const data={start:player.start,end:player.end||source().duration,ai,enqueue,source_ids:compare?sources().map(s=>s.id):[source().id],vocal_id:compare?null:vocal().id};
    const job=await post(`/projects/${state.project.id}/reports`,data);
    notify(job.reused?t('이미 접수된 동일한 분석입니다.'):enqueue?t('분석 대기열에 추가했습니다.'):t('분석 요청을 접수했습니다.'));
    await refresh();
  }finally{reportSubmitting=false;renderControls();}
}

document.addEventListener('click',async event=>{const b=event.target.closest('[data-action]');if(!b||b.disabled)return;const action=b.dataset.action,id=b.dataset.id;try{switch(action){
case 'new-project':projectMode='new';$('#project-modal-title').textContent=t('새 음악 프로젝트');$('#project-form input').value='';openModal('project');break;
case 'rename-project':projectMode='rename';$('#project-modal-title').textContent=t('프로젝트 이름 변경');$('#project-form input').value=state.project.name;openModal('project');break;
case 'close-modal':closeModal();break;case 'help':openModal('help');break;case 'theme':openModal('theme');break;
case 'settings':settingsTab(b.dataset.tab||'display');openModal('settings');break;case 'settings-tab':settingsTab(b.dataset.tab);break;
case 'theme-style':if(b.dataset.style==='default')document.documentElement.removeAttribute('data-ui-theme');else document.documentElement.setAttribute('data-ui-theme',b.dataset.style);try{localStorage.setItem('mas-theme-style',b.dataset.style);}catch{}syncPreferences();chart.draw();break;
case 'open-nav':$('#sidebar').classList.add('mobile-open');$('#sidebar-overlay').hidden=false;break;case 'close-nav':$('#sidebar').classList.remove('mobile-open');$('#sidebar-overlay').hidden=true;break;case 'collapse':togglePreference('collapsed');break;case 'dismiss-error':$('#error-banner').hidden=true;break;
case 'upload':$('#audio-upload').click();break;case 'play':player.playing?player.pause():player.play();break;case 'stop':player.stop();break;case 'loop':player.loop=!player.loop;syncRange();break;
case 'monitor-original':player.monitorOriginal=!player.monitorOriginal;player.applyMix();renderControls();break;
case 'mute':case 'solo':player.mixFor(id)[action]=!player.mixFor(id)[action];player.applyMix();renderControls();break;
case 'separate':b.disabled=true;try{await post(`/projects/${state.project.id}/tracks/${source().id}/separate`,{model:$('#separation-model').value});await refresh();}finally{renderControls();}break;
case 'analysis-toggle':state.enabled=!state.enabled;preference.set(`analysis-${state.source_id}`,state.enabled);renderControls();if(state.enabled){await analyze();await loadAnalyses();}break;
case 'analysis-mode':state.mode=b.dataset.mode;preference.set('analysis-mode',state.mode);renderControls();renderAnalysis();break;
case 'analyze':delete state.analyses[vocal()?.id];await analyze(true);await refresh();break;
case 'chart-kind':state.chartKind=b.dataset.kind;$$('[data-action="chart-kind"]').forEach(el=>el.classList.toggle('on',el===b));$('#chart-title').textContent={pitch:t('음정 윤곽'),rms:t('다이내믹'),centroid:t('스펙트럼 중심')}[state.chartKind];$('#chart-method').textContent={pitch:'pYIN',rms:'RMS',centroid:'STFT'}[state.chartKind];renderAnalysis();break;
case 'transform':$('#transform-form').reset();$('#semitones-label').textContent=t('0 반음');$('#transform-speed-label').textContent='1.00×';openModal('transform');break;
case 'delete-project':case 'delete-source':deleteTarget={type:action==='delete-project'?'project':'source',id};$('#delete-description').textContent=t('“{name}”의 원본과 모든 분리 트랙·분석 결과를 삭제합니다. 복구할 수 없습니다.',{name:b.dataset.name});openModal('delete');break;
case 'confirm-delete':b.disabled=true;try{await api(deleteTarget.type==='project'?`/projects/${deleteTarget.id}`:`/projects/${state.project.id}/tracks/${deleteTarget.id}`,{method:'DELETE'});location.assign(deleteTarget.type==='project'?'/dashboard':`/workspace?project=${state.project.id}`);}finally{b.disabled=false;}break;
case 'generate-report':await makeReport(b.dataset.ai==='true',b.dataset.enqueue==='true');break;
case 'export-report':if(state.report){const url=URL.createObjectURL(new Blob([JSON.stringify(state.report,null,2)],{type:'application/json'}));const link=document.createElement('a');link.href=url;link.download='mas-report.json';link.click();setTimeout(()=>URL.revokeObjectURL(url),1000);}break;
case 'vibrato':{const v=state.analyses[vocal().id].vibrato_candidates[Number(b.dataset.index)];$('#range-start').value=v.start;$('#range-end').value=v.end;player.loop=true;syncRange();player.seek(v.start);renderAnalysis();break;}
}}catch(e){showError(e.message);}});
document.addEventListener('click',e=>{const toggle=e.target.closest('[data-preference]');if(toggle)togglePreference(toggle.dataset.preference);if(e.target===modal)closeModal();});
document.addEventListener('keydown',e=>{if(!modal)return;if(e.key==='Escape'){closeModal();return;}if(e.key!=='Tab')return;const nodes=Array.from(modal.querySelectorAll('button:not(:disabled),input,select,summary,a[href]')).filter(el=>el.getClientRects().length),first=nodes[0],last=nodes[nodes.length-1];if(e.shiftKey&&document.activeElement===first){e.preventDefault();last?.focus();}if(!e.shiftKey&&document.activeElement===last){e.preventDefault();first?.focus();}});
$('#project-form').addEventListener('submit',async e=>{e.preventDefault();e.submitter.disabled=true;try{const name=$('#project-form input').value.trim()||t('새 음악 프로젝트');const p=projectMode==='new'?await post('/projects',{name}):await api(`/projects/${state.project.id}`,{method:'PATCH',body:JSON.stringify({name})});location.assign(`/workspace?project=${p.id}`);}catch(error){showError(error.message);}finally{e.submitter.disabled=false;}});
$('#transform-form').addEventListener('submit',async e=>{e.preventDefault();e.submitter.disabled=true;try{const f=new FormData(e.currentTarget);await post(`/projects/${state.project.id}/tracks/${source().id}/transform`,{vocal_id:vocal().id,semitones:Number(f.get('semitones')),speed:Number(f.get('speed')),normalize:f.has('normalize')});closeModal();await refresh();notify(t('현재 음원에 보컬 변형본을 추가했습니다.'));}catch(error){showError(error.message);}finally{e.submitter.disabled=false;}});
$('#audio-upload').addEventListener('change',e=>void uploadFiles(e.target.files));
$('#upload-zone')?.addEventListener('dragover',e=>{e.preventDefault();$('#upload-zone').classList.add('dragging');});$('#upload-zone')?.addEventListener('dragleave',()=>$('#upload-zone').classList.remove('dragging'));$('#upload-zone')?.addEventListener('drop',e=>{e.preventDefault();$('#upload-zone').classList.remove('dragging');void uploadFiles(e.dataTransfer.files);});
document.addEventListener('input',e=>{const el=e.target;if(el.dataset.volume){player.mixFor(el.dataset.volume).volume=Number(el.value);player.applyMix();$(`[data-volume-label="${el.dataset.volume}"]`).textContent=Math.round(Number(el.value)*100)+'%';}if(el.dataset.waveSeek)player.seek(Number(el.value));if(el.id==='transport-seek')player.seek(Number(el.value));if(el.id==='project-search')$$('[data-project-name]').forEach(card=>card.hidden=!card.dataset.projectName.toLocaleLowerCase().includes(el.value.toLocaleLowerCase()));if(el.name==='semitones')$('#semitones-label').textContent=t('{value} 반음',{value:(Number(el.value)>0?'+':'')+el.value});if(el.name==='speed')$('#transform-speed-label').textContent=Number(el.value).toFixed(2)+'×';});
document.addEventListener('change',async e=>{const el=e.target;try{if(el.id==='playback-speed')player.setSpeed(Number(el.value));if(el.id==='analysis-track'){state.vocalId=el.value;renderControls();renderAnalysis();if(state.enabled)await analyze();}if(el.id==='compare-vocals')renderAnalysis();if(el.id==='source-offset'){await api(`/projects/${state.project.id}/tracks/${source().id}`,{method:'PATCH',body:JSON.stringify({offset:Number(el.value)})});await refresh();}if(el.id==='range-start'||el.id==='range-end'){if(Number($('#range-end').value)<=Number($('#range-start').value)){showError(t('구간 끝은 시작보다 커야 합니다.'));return;}syncRange();renderAnalysis();}if(el.id==='report-history'&&el.value!==''){const r=await api(`/projects/${state.project.id}/reports/${Number(el.value)}`);showReport(r);}}catch(error){showError(error.message);}});
window.addEventListener('pagehide',()=>player.destroy());window.addEventListener('pageshow',e=>{if(e.persisted){player.setTracks(state.project?.id,playbackTracks());renderControls();}});
player.setTracks(state.project?.id,state.page==='workspace'?playbackTracks():[]);renderControls();renderAnalysis();renderHistory();
const pendingError=sessionStorage.getItem('mas-page-error');if(pendingError){showError(pendingError);sessionStorage.removeItem('mas-page-error');}
void refresh();setInterval(()=>void refresh(),2000);

try { if(sessionStorage.getItem('mas-reopen-language-settings')){sessionStorage.removeItem('mas-reopen-language-settings');settingsTab('language');openModal('settings');} } catch { /* Storage is optional. */ }
