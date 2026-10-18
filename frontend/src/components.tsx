import { useEffect, useRef, useState } from "react";
import type { ReactNode } from "react";
import { X, Waves } from "lucide-react";
import {
  CartesianGrid,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import type { Analysis, Track } from "./types";

export const COLORS = ["#60a5fa", "#c084fc", "#34d399"];
export const fmt = (value: number) =>
  `${Math.floor(Math.max(0, value) / 60)
    .toString()
    .padStart(2, "0")}:${Math.floor(Math.max(0, value) % 60)
    .toString()
    .padStart(2, "0")}`;
export function Modal({
  title,
  children,
  onClose,
  wide = false,
}: {
  title: string;
  children: ReactNode;
  onClose: () => void;
  wide?: boolean;
}) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const previous = document.activeElement as HTMLElement | null;
    ref.current?.querySelector<HTMLElement>("button,input,select")?.focus();
    function key(e: KeyboardEvent) {
      if (e.key === "Escape") onClose();
      if (e.key === "Tab") {
        const els = Array.from(
          ref.current?.querySelectorAll<HTMLElement>(
            "button:not(:disabled),input,select,textarea,a[href]",
          ) || [],
        ).filter((x) => x.getClientRects().length);
        const first = els[0],
          last = els[els.length - 1];
        if (e.shiftKey && document.activeElement === first) {
          e.preventDefault();
          last?.focus();
        }
        if (!e.shiftKey && document.activeElement === last) {
          e.preventDefault();
          first?.focus();
        }
      }
    }
    document.addEventListener("keydown", key);
    return () => {
      document.removeEventListener("keydown", key);
      previous?.focus();
    };
  }, [onClose]);
  return (
    <div
      className="modal-overlay"
      onMouseDown={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <div
        className={`modal ${wide ? "wide" : ""}`}
        ref={ref}
        role="dialog"
        aria-modal="true"
        aria-label={title}
      >
        {children}
      </div>
    </div>
  );
}
export function ModalHead({
  title,
  subtitle,
  onClose,
}: {
  title: string;
  subtitle?: string;
  onClose: () => void;
}) {
  return (
    <header className="modal-head">
      <div>
        <h3>{title}</h3>
        {subtitle && <p>{subtitle}</p>}
      </div>
      <button className="icon-btn" onClick={onClose} aria-label="닫기">
        <X size={18} />
      </button>
    </header>
  );
}
export function Empty({
  title,
  description,
  children,
}: {
  title: string;
  description: string;
  children?: ReactNode;
}) {
  return (
    <div className="empty">
      <div className="empty-icon">
        <Waves size={27} />
      </div>
      <h3>{title}</h3>
      <p>{description}</p>
      {children}
    </div>
  );
}

export function Waveform({
  analysis,
  color,
  position,
  offset,
  duration,
  onSeek,
}: {
  analysis?: Analysis;
  color: string;
  position: number;
  offset: number;
  duration: number;
  onSeek: (n: number) => void;
}) {
  const wave = analysis?.waveform;
  const percentage = duration
    ? Math.max(0, Math.min(100, ((position + offset) / duration) * 100))
    : 0;
  const path = wave
    ? wave.max
        .map((v, i) => `M${i} ${32 - v * 29}V${32 - wave.min[i] * 29}`)
        .join(" ")
    : "";
  return (
    <div className="waveform-wrap">
      <svg
        className="waveform"
        viewBox={`0 0 ${wave?.max.length || 1000} 64`}
        preserveAspectRatio="none"
        aria-hidden="true"
      >
        <path d={path} stroke={color} strokeWidth="1.6" opacity=".65" />
        <line
          x1="0"
          y1="32"
          x2={wave?.max.length || 1000}
          y2="32"
          stroke="currentColor"
          opacity=".08"
        />
      </svg>
      {wave && (
        <>
          <span className="wavehead" style={{ left: `${percentage}%` }} />
          <input
            aria-label="트랙 파형 탐색"
            type="range"
            min={0}
            max={duration}
            step={0.01}
            value={Math.max(0, Math.min(duration, position + offset))}
            onChange={(e) => onSeek(Number(e.target.value) - offset)}
          />
        </>
      )}
      {!wave && (
        <span className="wave-wait">분석 후 실제 파형이 표시됩니다</span>
      )}
    </div>
  );
}

export function AnalysisChart({
  tracks,
  analyses,
  kind,
  start,
  end,
}: {
  tracks: Track[];
  analyses: Record<string, Analysis>;
  kind: string;
  start: number;
  end: number;
}) {
  const rows: Record<string, number | null>[] = [];
  const available = tracks.filter((t) => analyses[t.id]);
  const steps = Math.min(
    1400,
    Math.max(2, Math.ceil(((end - start) * 22050) / 512)),
  );
  // Common timeline sampling keeps offset tracks continuous while preserving
  // nulls for genuinely unvoiced/out-of-range frames.
  for (let i = 0; i <= steps; i++) {
    const at = start + ((end - start) * i) / steps;
    const row: Record<string, number | null> = { time: at };
    available.forEach((t) => {
      const a = analyses[t.id];
      const source = at + t.offset;
      const times = kind === "pitch" ? a.series.pitch_time : a.series.time;
      const values =
        kind === "pitch"
          ? a.series.pitch_midi
          : kind === "rms"
            ? a.series.rms_db
            : a.series.centroid_hz;
      const dt = times.length > 1 ? times[1] - times[0] : 512 / 22050;
      const index = Math.round(source / dt);
      row[t.id] =
        source < 0 || source > a.summary.duration || index >= values.length
          ? null
          : values[index];
    });
    if (available.length) rows.push(row);
  }
  if (!rows.length)
    return (
      <div className="chart-empty">
        선택 구간의 분석 데이터를 기다리고 있습니다.
      </div>
    );
  return (
    <div
      className="chart"
      role="img"
      aria-label={
        kind === "pitch"
          ? "시간에 따른 보컬 음정 비교 그래프"
          : kind === "rms"
            ? "시간에 따른 RMS 다이내믹 비교 그래프"
            : "시간에 따른 스펙트럼 중심 비교 그래프"
      }
    >
      <ResponsiveContainer width="100%" height="100%">
        <LineChart
          data={rows}
          margin={{ top: 10, right: 12, bottom: 5, left: 0 }}
        >
          <CartesianGrid
            stroke="var(--grid)"
            strokeDasharray="3 5"
            vertical={false}
          />
          <XAxis
            dataKey="time"
            type="number"
            domain={[start, end]}
            tickFormatter={fmt}
            stroke="var(--muted)"
            tickLine={false}
            axisLine={false}
            tick={{ fontSize: 10 }}
            minTickGap={30}
          />
          <YAxis
            width={48}
            domain={["auto", "auto"]}
            stroke="var(--muted)"
            tickLine={false}
            axisLine={false}
            tick={{ fontSize: 10 }}
            tickFormatter={(v) =>
              kind === "pitch" ? midiNote(v) : `${Math.round(v)}`
            }
          />
          <Tooltip
            labelFormatter={(v) => `${Number(v).toFixed(2)}초`}
            formatter={(v) =>
              v == null
                ? "—"
                : kind === "pitch"
                  ? `${midiNote(Number(v))} (${Number(v).toFixed(1)} MIDI)`
                  : `${Number(v).toFixed(1)} ${kind === "rms" ? "dBFS" : "Hz"}`
            }
            contentStyle={{
              background: "var(--tooltip)",
              border: "1px solid var(--border)",
              borderRadius: 12,
              fontSize: 11,
              color: "var(--text)",
            }}
          />
          {tracks.map((t, i) => (
            <Line
              key={t.id}
              dataKey={t.id}
              name={t.name}
              stroke={COLORS[i]}
              dot={false}
              strokeWidth={1.7}
              connectNulls={false}
              isAnimationActive={false}
            />
          ))}
        </LineChart>
      </ResponsiveContainer>
    </div>
  );
}
function midiNote(m: number) {
  const n = Math.round(m);
  return (
    ["C", "C♯", "D", "D♯", "E", "F", "F♯", "G", "G♯", "A", "A♯", "B"][
      ((n % 12) + 12) % 12
    ] +
    (Math.floor(n / 12) - 1)
  );
}

export function Spectrogram({ analysis }: { analysis: Analysis }) {
  const ref = useRef<HTMLCanvasElement>(null);
  useEffect(() => {
    const c = ref.current;
    if (!c) return;
    const db = analysis.spectrogram.db;
    const w = db[0].length,
      h = db.length;
    c.width = w;
    c.height = h;
    const ctx = c.getContext("2d")!;
    const img = ctx.createImageData(w, h);
    for (let y = 0; y < h; y++)
      for (let x = 0; x < w; x++) {
        const v = Math.max(0, Math.min(1, (db[h - 1 - y][x] + 80) / 80));
        const p = (y * w + x) * 4;
        img.data[p] = Math.round(Math.max(0, v - 0.35) * 390);
        img.data[p + 1] = Math.round(v * v * 210);
        img.data[p + 2] = Math.round(38 + Math.sin(v * Math.PI * 0.8) * 175);
        img.data[p + 3] = 255;
      }
    ctx.putImageData(img, 0, 0);
  }, [analysis]);
  return (
    <div className="spectro">
      <div className="spectro-labels">
        <span>11 kHz</span>
        <span>1 kHz</span>
        <span>65 Hz</span>
      </div>
      <div>
        <canvas
          ref={ref}
          aria-label="실제 STFT를 계산한 로그 주파수 스펙트로그램"
        />
        <div className="spectro-time">
          <span>00:00</span>
          <span>{fmt(analysis.summary.duration)}</span>
        </div>
      </div>
      <div className="spectro-key">
        <span>0</span>
        <i />
        <span>−80 dB</span>
      </div>
    </div>
  );
}

export function ThemePicker() {
  const [style, setStyle] = useState(
    localStorage.getItem("mas-theme-style") || "default",
  );
  const [dark, setDark] = useState(
    document.documentElement.getAttribute("data-ui-theme-mode") !== "light",
  );
  function choose(s: string) {
    setStyle(s);
    localStorage.setItem("mas-theme-style", s);
    if (s === "default")
      document.documentElement.removeAttribute("data-ui-theme");
    else document.documentElement.setAttribute("data-ui-theme", s);
  }
  function mode() {
    const next = !dark;
    setDark(next);
    localStorage.setItem("mas-theme", next ? "dark" : "light");
    document.documentElement.style.colorScheme = next ? "dark" : "light";
    if (next) document.documentElement.removeAttribute("data-ui-theme-mode");
    else document.documentElement.setAttribute("data-ui-theme-mode", "light");
  }
  return (
    <>
      <div className="pmd-theme-body">
        {[
          ["default", "오로라"],
          ["mesh", "그라디언트 메시"],
          ["apple", "애플 글래스"],
          ["mono", "미니멀 플랫"],
        ].map(([id, label]) => (
          <button
            key={id}
            onClick={() => choose(id)}
            aria-pressed={style === id}
            className={`pmd-theme-option ${style === id ? "selected" : ""}`}
          >
            <span className={`pmd-theme-swatch pmd-swatch-${id}`} />
            <span>{label}</span>
          </button>
        ))}
      </div>
      <div className="pmd-theme-mode-row">
        <span>다크 모드</span>
        <button
          onClick={mode}
          role="switch"
          aria-checked={dark}
          aria-label="다크 모드"
          className={`pmd-mode-switch ${dark ? "on" : ""}`}
        />
      </div>
    </>
  );
}
