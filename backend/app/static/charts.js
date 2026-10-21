import { t } from './i18n.js';
import { fmt } from './api.js';
export const COLORS = ['#60a5fa', '#c084fc', '#34d399'];
const midiNote = m => { const n = Math.round(m); return ['C','C♯','D','D♯','E','F','F♯','G','G♯','A','A♯','B'][((n % 12) + 12) % 12] + (Math.floor(n / 12) - 1); };

export function drawWaveform(host, analysis) {
  if (!host || !analysis) return;
  const wave = analysis.waveform;
  host.querySelector('svg').setAttribute('viewBox', `0 0 ${wave.max.length} 64`);
  host.querySelector('path').setAttribute('d', wave.max.map((v, i) => `M${i} ${32 - v * 29}V${32 - wave.min[i] * 29}`).join(' '));
  host.querySelector('line').setAttribute('x2', wave.max.length);
  host.querySelector('.wavehead').hidden = false;
  host.querySelector('input').disabled = false;
  host.querySelector('.wave-wait').hidden = true;
}

/** Native Canvas graph, including real unvoiced gaps and manual time offsets. */
export class ComparisonChart {
  constructor(host) {
    this.host = host;
    if (!host) return;
    this.canvas = host.querySelector('canvas');
    this.tooltip = host.querySelector('.chart-tooltip');
    this.resize = new ResizeObserver(() => this.draw()); this.resize.observe(host);
    this.canvas.addEventListener('pointermove', event => this.hover(event));
    this.canvas.addEventListener('pointerleave', () => { this.tooltip.hidden = true; });
  }
  update(tracks, analyses, kind, start, end) { this.data = { tracks, analyses, kind, start, end }; this.draw(); }
  draw() {
    if (!this.host || !this.data) return;
    const { tracks, analyses, kind, start, end } = this.data;
    const width = this.host.clientWidth, height = this.host.clientHeight;
    if (!width || !height) return;
    const ratio = window.devicePixelRatio || 1;
    this.canvas.width = Math.round(width * ratio); this.canvas.height = Math.round(height * ratio);
    this.canvas.style.width = `${width}px`; this.canvas.style.height = `${height}px`;
    const ctx = this.canvas.getContext('2d'); ctx.scale(ratio, ratio);
    const series = tracks.map((track, i) => {
      const a = analyses[track.id]; if (!a) return { track, color: COLORS[i], points: [] };
      const times = kind === 'pitch' ? a.series.pitch_time : a.series.time;
      const values = kind === 'pitch' ? a.series.pitch_midi : kind === 'rms' ? a.series.rms_db : a.series.centroid_hz;
      return { track, color: COLORS[i], points: times.map((time, j) => ({ time: time - track.offset, value: values[j] })).filter(p => p.time >= start && p.time <= end) };
    });
    this.series = series;
    const values = series.flatMap(s => s.points.map(p => p.value)).filter(Number.isFinite);
    const empty = this.host.querySelector('.canvas-empty');
    empty.hidden = !!values.length;
    empty.textContent = tracks.some(t => analyses[t.id]) ? t('선택 구간에서 유효한 측정값을 찾지 못했습니다.') : t('보컬 분석을 완료하면 그래프가 표시됩니다.');
    if (!values.length) { this.box = null; this.tooltip.hidden = true; return; }
    let low = Math.min(...values), high = Math.max(...values);
    const padding = Math.max((high - low) * .12, kind === 'pitch' ? 1 : 2);
    low -= padding; high += padding;
    const box = { left: 51, top: 15, width: Math.max(1, width - 67), height: Math.max(1, height - 43) };
    this.box = box;
    const x = time => box.left + (time - start) / Math.max(.001, end - start) * box.width;
    const y = value => box.top + (high - value) / (high - low) * box.height;
    const style = getComputedStyle(document.documentElement);
    ctx.font = "10px Inter, system-ui, sans-serif"; ctx.fillStyle = style.getPropertyValue('--muted').trim();
    for (let i = 0; i <= 4; i++) {
      const value = low + (high - low) * i / 4, yy = y(value);
      ctx.strokeStyle = style.getPropertyValue('--grid').trim(); ctx.setLineDash([3, 5]); ctx.beginPath(); ctx.moveTo(box.left, yy); ctx.lineTo(box.left + box.width, yy); ctx.stroke();
      ctx.textAlign = 'right'; ctx.fillText(kind === 'pitch' ? midiNote(value) : Math.round(value).toString(), box.left - 9, yy + 3);
      const time = start + (end - start) * i / 4;
      ctx.textAlign = i === 0 ? 'left' : i === 4 ? 'right' : 'center'; ctx.fillText(fmt(time), x(time), height - 8);
    }
    ctx.setLineDash([]);
    ctx.save(); ctx.beginPath(); ctx.rect(box.left, box.top, box.width, box.height); ctx.clip();
    for (const s of series) {
      ctx.strokeStyle = s.color; ctx.lineWidth = 1.7; ctx.beginPath(); let pen = false;
      for (const p of s.points) {
        if (!Number.isFinite(p.value)) { pen = false; continue; }
        if (pen) ctx.lineTo(x(p.time), y(p.value)); else { ctx.moveTo(x(p.time), y(p.value)); pen = true; }
      }
      ctx.stroke();
    }
    ctx.restore();
  }
  hover(event) {
    if (!this.box || !this.series) return;
    const { start, end, kind } = this.data;
    const rect = this.canvas.getBoundingClientRect();
    const px = event.clientX - rect.left;
    const time = start + Math.max(0, Math.min(1, (px - this.box.left) / this.box.width)) * (end - start);
    const lines = [t('{time}초',{time:time.toFixed(2)})];
    for (const s of this.series) {
      const p = s.points.reduce((best, next) => !best || Math.abs(next.time - time) < Math.abs(best.time - time) ? next : best, null);
      const value = Number.isFinite(p?.value) ? kind === 'pitch' ? `${midiNote(p.value)} (${p.value.toFixed(1)} MIDI)` : `${p.value.toFixed(1)} ${kind === 'rms' ? 'dBFS' : 'Hz'}` : '—';
      lines.push(`${s.track.name}: ${value}`);
    }
    this.tooltip.textContent = lines.join('\n'); this.tooltip.hidden = false;
    this.tooltip.style.left = `${Math.max(6, Math.min(px + 10, this.host.clientWidth - 230))}px`;
  }
}

export function drawSpectrogram(canvas, analysis) {
  if (!canvas || !analysis) return;
  const db = analysis.spectrogram.db, width = db[0].length, height = db.length;
  canvas.width = width; canvas.height = height;
  const ctx = canvas.getContext('2d'), img = ctx.createImageData(width, height);
  for (let y = 0; y < height; y++) for (let x = 0; x < width; x++) {
    const v = Math.max(0, Math.min(1, (db[height - 1 - y][x] + 80) / 80)), p = (y * width + x) * 4;
    img.data[p] = Math.round(Math.max(0, v - .35) * 390); img.data[p+1] = Math.round(v*v*210); img.data[p+2] = Math.round(38 + Math.sin(v * Math.PI * .8) * 175); img.data[p+3] = 255;
  }
  ctx.putImageData(img, 0, 0);
}
