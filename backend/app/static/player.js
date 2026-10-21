import { t } from './i18n.js';


/** Shared timeline; audio instances survive DOM fragment refreshes. */
export class StudioPlayer {
  constructor(onTick, onError) {
    this.audio = new Map();
    this.mix = new Map();
    this.tracks = [];
    this.selected = '';
    this.playing = false;
    this.position = 0;
    this.speed = 1;
    this.loop = false;
    this.start = 0;
    this.end = 0;
    this.normalize = true;
    this.multi = true;
    this.monitorOriginal = false;
    this.onTick = onTick;
    this.onError = onError;
    this.timer = null;
  }
  get duration() { return Math.max(0, ...this.tracks.filter(t => t.status === 'ready').map(t => Math.max(0, t.duration - t.offset))); }
  get active() { return this.tracks.find(t => t.id === this.selected) || this.tracks[0]; }
  mixFor(id) { if (!this.mix.has(id)) this.mix.set(id, { volume: .8, mute: false, solo: false }); return this.mix.get(id); }
  setTracks(pid, tracks) {
    this.tracks = tracks;
    if (!tracks.some(t => t.id === this.selected)) this.selected = tracks[0]?.id || '';
    for (const track of tracks.filter(t => t.status === 'ready')) {
      if (this.audio.has(track.id)) continue;
      const audio = new Audio(track.url);
      audio.preload = 'metadata';
      audio.addEventListener('error', () => { this.pause(); this.onError(t('오디오 재생 실패: 브라우저 지원 또는 서버 연결을 확인하세요.')); });
      this.audio.set(track.id, audio);
    }
    for (const [id, audio] of this.audio) {
      if (tracks.some(t => t.id === id && t.status === 'ready')) continue;
      audio.pause(); audio.removeAttribute('src'); audio.load(); this.audio.delete(id);
    }
    this.position = Math.min(this.position, this.duration);
    for (const t of tracks) {
      if (!this.mix.has(t.id)) this.mix.set(t.id, { volume: .8, mute: !!t.derived, solo: false });
    }
    this.applyMix();
  }
  applyMix() {
    const ready = this.tracks.filter(t => t.status === 'ready');
    const hasSolo = ready.some(t => this.mixFor(t.id).solo);
    const lowest = Math.min(...ready.map(t => t.summary?.rms_db ?? 0));
    for (const t of ready) {
      const audio = this.audio.get(t.id); if (!audio) continue;
      const mix = this.mixFor(t.id);
      const gain = this.normalize ? Math.min(1, 10 ** ((lowest - (t.summary?.rms_db ?? 0)) / 20)) : 1;
      audio.volume = Math.max(0, Math.min(1, mix.volume * gain));
      const stems = ready.some(t => t.kind !== 'original');
      const originalOnly = this.monitorOriginal || !stems;
      audio.muted = mix.mute || (hasSolo ? !mix.solo : originalOnly ? t.kind !== 'original' : t.kind === 'original');
      audio.playbackRate = this.speed;
      audio.preservesPitch = true;
    }
  }
  seek(value) {
    this.position = Math.max(0, Math.min(this.duration, value));
    this.anchor = { position: this.position, time: performance.now() };
    for (const t of this.tracks) {
      const audio = this.audio.get(t.id);
      if (audio?.readyState) audio.currentTime = Math.max(0, Math.min(t.duration, this.position + t.offset));
    }
    this.onTick(this);
  }
  setSpeed(speed) { this.seek(this.position); this.speed = speed; this.applyMix(); }
  play() {
    if (!this.audio.size) return;
    if (this.position >= this.duration || this.loop && (this.position < this.start || this.position >= this.end)) this.seek(this.loop ? this.start : 0);
    this.playing = true;
    this.anchor = { position: this.position, time: performance.now() };
    this.applyMix();
    this.syncAudio(); // Execute in the original click gesture to unlock browser audio.
    clearInterval(this.timer);
    this.timer = setInterval(() => this.tick(), 30);
    this.onTick(this);
  }
  syncAudio() {
    for (const t of this.tracks) {
      const audio = this.audio.get(t.id); if (!audio) continue;
      const at = this.position + t.offset;
      if (at < 0 || at >= t.duration) { audio.pause(); continue; }
      if (audio.readyState && Math.abs(audio.currentTime - at) > .2) audio.currentTime = at;
      if (audio.paused) audio.play().catch(() => { if (this.playing) { this.pause(); this.onError(t('재생을 시작할 수 없습니다. 브라우저 오디오 권한이나 서버 연결을 확인하세요.')); } });
    }
  }
  tick() {
    if (!this.playing) return;
    this.position = this.anchor.position + (performance.now() - this.anchor.time) / 1000 * this.speed;
    const boundary = this.loop ? Math.min(this.end || this.duration, this.duration) : this.duration;
    if (this.position >= boundary) {
      if (this.loop && boundary > this.start) this.seek(this.start);
      else { this.position = boundary; this.pause(); return; }
    }
    this.syncAudio();
    this.onTick(this);
  }
  pause() { this.playing = false; clearInterval(this.timer); this.timer = null; for (const audio of this.audio.values()) audio.pause(); this.onTick(this); }
  stop() { this.pause(); this.seek(0); }
  destroy() { this.pause(); for (const audio of this.audio.values()) { audio.removeAttribute('src'); audio.load(); } this.audio.clear(); }
}
