export type Summary = {
  duration: number;
  sample_rate: number;
  rms_db: number;
  dynamic_range_db: number;
  peak_db: number;
  clipping_percent: number;
  voiced_percent: number;
  pitch_confidence: number;
  pitch_low_note: string | null;
  pitch_high_note: string | null;
  pitch_median_hz: number | null;
  note_center_deviation_cents: number | null;
  spectral_centroid_hz: number;
  spectral_flatness: number;
  onset_count: number;
  tempo_candidate_bpm: number | null;
};
export type Track = {
  id: string;
  name: string;
  status: "queued" | "processing" | "ready" | "failed";
  duration: number;
  offset: number;
  size: number;
  created_at: string;
  error?: string;
  job_id?: string;
  cache_hit?: boolean;
  summary?: Summary;
  source_id?: string;
  transform?: { semitones: number; speed: number; normalize: boolean };
};
export type Project = {
  id: string;
  name: string;
  created_at: string;
  updated_at: string;
  tracks: Track[];
  reports?: Report[];
};
export type Analysis = {
  engine: string;
  summary: Summary;
  warnings: string[];
  series: {
    time: number[];
    rms_db: number[];
    pitch_time: number[];
    pitch_hz: (number | null)[];
    pitch_midi: (number | null)[];
    confidence: number[];
    centroid_hz: number[];
  };
  waveform: { min: number[]; max: number[] };
  spectrogram: {
    db: number[][];
    duration: number;
    min_db: number;
    low_hz: number;
    high_hz: number;
  };
  onsets: number[];
  vibrato_candidates: {
    start: number;
    end: number;
    rate_hz: number;
    width_cents: number;
    periodicity: number;
  }[];
};
export type Report = {
  content: string;
  evidence: unknown[];
  kind: "ai" | "measured";
  model: string | null;
  created_at: string;
};
export type Job = {
  id: string;
  status: "queued" | "processing" | "done" | "failed";
  progress: number;
  message: string;
  result?: Report;
};
export type Health = {
  status: string;
  database: string;
  database_error: string | null;
  ssh_enabled: boolean;
  ai_configured: boolean;
  ai_model: string;
  engine: string;
  device: string;
  storage: string;
  max_upload_mb: number;
  max_duration_seconds: number;
};
