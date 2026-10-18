import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { ChangeEvent } from "react";
import {
  Activity,
  ArrowDownToLine,
  ArrowLeftRight,
  AudioLines,
  BarChart3,
  Check,
  ChevronDown,
  ChevronsLeft,
  CircleHelp,
  Clock3,
  Cpu,
  Database,
  FolderOpen,
  Gauge,
  Headphones,
  LayoutDashboard,
  Menu,
  Mic2,
  MoreHorizontal,
  Pause,
  Play,
  Plus,
  Repeat2,
  Search,
  Settings,
  SlidersHorizontal,
  Sparkles,
  Square,
  Trash2,
  Upload,
  Volume2,
  Waves,
  X,
  Palette,
  LoaderCircle,
} from "lucide-react";
import { api, post, trackUrl } from "./api";
import {
  AnalysisChart,
  COLORS,
  Empty,
  fmt,
  Modal,
  ModalHead,
  Spectrogram,
  ThemePicker,
  Waveform,
} from "./components";
import type { Analysis, Health, Job, Project, Report, Track } from "./types";

const NAV = [
  {
    id: "dashboard",
    label: "대시보드",
    icon: LayoutDashboard,
    sub: "프로젝트",
  },
  {
    id: "workspace",
    label: "워크스페이스",
    icon: AudioLines,
    sub: "녹음과 트랙",
  },
  { id: "compare", label: "보컬 비교", icon: ArrowLeftRight, sub: "A / B / C" },
  { id: "analysis", label: "상세 분석", icon: BarChart3, sub: "음정과 음색" },
  { id: "report", label: "AI 평가", icon: Sparkles, sub: "측정 기반 피드백" },
];
const SETTINGS = [
  { id: "display", label: "화면", desc: "테마 · 레이아웃", icon: Palette },
  { id: "audio", label: "오디오", desc: "재생 · 음량 매칭", icon: Headphones },
  {
    id: "analysis",
    label: "분석 엔진",
    desc: "알고리즘 · 처리 장치",
    icon: Cpu,
  },
  {
    id: "database",
    label: "데이터 · 연결",
    desc: "MongoDB · SSH",
    icon: Database,
  },
  { id: "ai", label: "AI 평가", desc: "모델 · 연결 상태", icon: Sparkles },
];
type Mix = { volume: number; mute: boolean; solo: boolean };
function useSaved<T>(key: string, fallback: T): [T, (v: T) => void] {
  const [v, setV] = useState<T>(() => {
    try {
      return JSON.parse(localStorage.getItem(key) || "null") ?? fallback;
    } catch {
      return fallback;
    }
  });
  return [
    v,
    (next: T) => {
      setV(next);
      localStorage.setItem(key, JSON.stringify(next));
    },
  ];
}

export default function App() {
  const [page, setPage] = useState("workspace");
  const [projects, setProjects] = useState<Project[]>([]);
  const [pid, setPid] = useSaved<string>("mas-project", "");
  const [health, setHealth] = useState<Health>();
  const [analyses, setAnalyses] = useState<Record<string, Analysis>>({});
  const [jobs, setJobs] = useState<Record<string, Job>>({});
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [toast, setToast] = useState("");
  const [modal, setModal] = useState("");
  const [settingTab, setSettingTab] = useState("display");
  const [collapsed, setCollapsed] = useSaved("mas-sidebar-collapsed", false);
  const [mobileNav, setMobileNav] = useState(false);
  const [showOverview, setShowOverview] = useSaved("mas-overview", true);
  const [normalize, setNormalize] = useSaved("mas-normalize", true);
  const [multi, setMulti] = useSaved("mas-multitrack", false);
  const [selected, setSelected] = useState("");
  const [mix, setMix] = useState<Record<string, Mix>>({});
  const [playing, setPlaying] = useState(false);
  const [position, setPosition] = useState(0);
  const [speed, setSpeed] = useState(1);
  const [loop, setLoop] = useState(false);
  const [start, setStart] = useState(0);
  const [end, setEnd] = useState(0);
  const [chartKind, setChartKind] = useState("pitch");
  const [uploading, setUploading] = useState(false);
  const [dragging, setDragging] = useState(false);
  const [search, setSearch] = useState("");
  const [projectName, setProjectName] = useState("");
  const [deleteTarget, setDeleteTarget] = useState<{
    type: "project" | "track";
    id: string;
    name: string;
  } | null>(null);
  const [report, setReport] = useState<Report>();
  const [reportJob, setReportJob] = useState<Job>();
  const [transformId, setTransformId] = useState("");
  const [semitones, setSemitones] = useState(0);
  const [transformSpeed, setTransformSpeed] = useState(1);
  const [transformNormalize, setTransformNormalize] = useState(true);
  const input = useRef<HTMLInputElement>(null);
  const audioRefs = useRef<Record<string, HTMLAudioElement>>({});
  const clock = useRef({ position: 0, time: performance.now() });
  const timeRef = useRef(0);
  const project = projects.find((p) => p.id === pid);
  const trackSignature = JSON.stringify(project?.tracks || []);
  const tracks = useMemo<Track[]>(
    () => JSON.parse(trackSignature),
    [trackSignature],
  );
  const active = tracks.find((t) => t.id === selected) || tracks[0];
  const ready = useMemo(
    () => tracks.filter((t) => t.status === "ready"),
    [tracks],
  );
  const duration = Math.max(
    0,
    ...ready.map((t) => Math.max(0, t.duration - t.offset)),
  );
  const activeAnalysis = active ? analyses[active.id] : undefined;
  const activeSummary = activeAnalysis?.summary;
  const busy = tracks.some(
    (t) => t.status === "queued" || t.status === "processing",
  );
  const closeModal = useCallback(() => setModal(""), []);
  const notify = (message: string) => {
    setToast(message);
  };
  const stop = useCallback(() => {
    setPlaying(false);
    Object.values(audioRefs.current).forEach((a) => a.pause());
  }, []);

  useEffect(() => {
    document.querySelector(".content-scroll")?.scrollTo(0, 0);
  }, [page, pid]);

  const refresh = useCallback(async () => {
    try {
      const h = await api<Health>("/health");
      setHealth(h);
      const p = await api<Project[]>("/projects");
      setProjects(p);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setLoading(false);
    }
  }, []);
  useEffect(() => {
    void refresh();
    const timer = setInterval(() => {
      void refresh();
    }, 2000);
    return () => clearInterval(timer);
  }, [refresh]);
  useEffect(() => {
    if (toast) {
      const t = setTimeout(() => setToast(""), 4500);
      return () => clearTimeout(t);
    }
  }, [toast]);
  useEffect(() => {
    document.documentElement.classList.toggle("sidebar-collapsed", collapsed);
  }, [collapsed]);
  useEffect(() => {
    setSelected("");
    setPosition(0);
    timeRef.current = 0;
    setStart(0);
    setEnd(0);
    setReport(undefined);
    setReportJob(undefined);
    stop();
  }, [pid, stop]);
  useEffect(() => {
    if (duration && end === 0) setEnd(duration);
  }, [duration, end]);
  useEffect(() => {
    const need = tracks.filter((t) => t.status === "ready" && !analyses[t.id]);
    if (!need.length) return;
    let cancelled = false;
    Promise.all(
      need.map(
        async (t) =>
          [
            t.id,
            await api<Analysis>(`/projects/${pid}/tracks/${t.id}/analysis`),
          ] as const,
      ),
    )
      .then((entries) => {
        if (!cancelled)
          setAnalyses((old) => ({ ...old, ...Object.fromEntries(entries) }));
      })
      .catch((e) => setError(e.message));
    return () => {
      cancelled = true;
    };
  }, [projects, pid, analyses]);
  useEffect(() => {
    const ids = tracks
      .filter((t) => t.status === "queued" || t.status === "processing")
      .map((t) => t.job_id)
      .filter(Boolean) as string[];
    if (!ids.length) return;
    let off = false;
    Promise.all(
      ids.map((id) => api<Job>(`/jobs/${id}`).catch(() => null)),
    ).then((js) => {
      if (!off)
        setJobs((old) => ({
          ...old,
          ...Object.fromEntries(js.filter(Boolean).map((j) => [j!.id, j!])),
        }));
    });
    return () => {
      off = true;
    };
  }, [projects, pid]);
  useEffect(() => {
    const refs = audioRefs.current;
    ready.forEach((t) => {
      if (!refs[t.id]) {
        const a = new Audio(trackUrl(pid, t.id));
        a.preload = "metadata";
        a.addEventListener("error", () =>
          setError(
            "오디오 재생 실패: 브라우저 지원 또는 서버 연결을 확인하세요.",
          ),
        );
        refs[t.id] = a;
      }
    });
    Object.keys(refs).forEach((id) => {
      if (!ready.some((t) => t.id === id)) {
        refs[id].pause();
        refs[id].removeAttribute("src");
        refs[id].load();
        delete refs[id];
      }
    });
  }, [tracks, pid]);
  useEffect(
    () => () => {
      Object.values(audioRefs.current).forEach((a) => {
        a.pause();
        a.removeAttribute("src");
        a.load();
      });
    },
    [],
  );

  function seek(value: number) {
    const p = Math.max(0, Math.min(duration, value));
    timeRef.current = p;
    setPosition(p);
    clock.current = { position: p, time: performance.now() };
    Object.entries(audioRefs.current).forEach(([id, a]) => {
      const t = tracks.find((t) => t.id === id);
      if (t && a.readyState > 0)
        a.currentTime = Math.max(0, Math.min(t.duration, p + t.offset));
    });
  }
  const mixFor = (id: string): Mix =>
    mix[id] || { volume: 0.8, mute: false, solo: false };
  function setTrackMix(id: string, values: Partial<Mix>) {
    setMix((prev) => ({
      ...prev,
      [id]: {
        ...(prev[id] || { volume: 0.8, mute: false, solo: false }),
        ...values,
      },
    }));
  }
  useEffect(() => {
    const hasSolo = ready.some((t) => mix[t.id]?.solo);
    const lowest = Math.min(...ready.map((t) => t.summary?.rms_db ?? 0));
    ready.forEach((t) => {
      const a = audioRefs.current[t.id];
      if (!a) return;
      const m = mix[t.id] || { volume: 0.8, mute: false, solo: false };
      const matched = normalize
        ? Math.min(1, 10 ** ((lowest - (t.summary?.rms_db ?? 0)) / 20))
        : 1;
      a.volume = Math.max(0, Math.min(1, m.volume * matched));
      a.muted = m.mute || (hasSolo ? !m.solo : !multi && t.id !== active?.id);
      a.playbackRate = speed;
      a.preservesPitch = true;
    });
  }, [mix, normalize, multi, active?.id, speed, tracks]);
  useEffect(() => {
    if (!playing) return;
    clock.current = { position: timeRef.current, time: performance.now() };
    let frame = 0,
      lastPaint = 0;
    const failed = new Set<string>();
    function tick(now: number) {
      let pos =
        clock.current.position + ((now - clock.current.time) / 1000) * speed;
      const boundary = loop ? Math.min(end || duration, duration) : duration;
      if (pos >= boundary) {
        if (loop && boundary > start) {
          pos = start;
          clock.current = { position: pos, time: now };
        } else {
          stop();
          timeRef.current = boundary;
          setPosition(boundary);
          return;
        }
      }
      timeRef.current = pos;
      ready.forEach((t) => {
        const a = audioRefs.current[t.id];
        if (!a) return;
        const at = pos + t.offset;
        if (at < 0 || at >= t.duration) {
          a.pause();
          return;
        }
        if (a.readyState > 0 && Math.abs(a.currentTime - at) > 0.2)
          a.currentTime = at;
        if (a.paused && !failed.has(t.id))
          a.play().catch(() => {
            failed.add(t.id);
            setError(
              "재생을 시작할 수 없습니다. 브라우저 오디오 권한이나 연결을 확인하세요.",
            );
            stop();
          });
      });
      if (now - lastPaint > 80) {
        setPosition(pos);
        lastPaint = now;
      }
      frame = requestAnimationFrame(tick);
    }
    frame = requestAnimationFrame(tick);
    return () => {
      cancelAnimationFrame(frame);
      Object.values(audioRefs.current).forEach((a) => a.pause());
    };
  }, [playing, speed, loop, start, end, duration, tracks, stop]);
  useEffect(() => {
    if (!reportJob || ["done", "failed"].includes(reportJob.status)) return;
    const id = reportJob.id;
    let cancelled = false;
    const t = setInterval(() => {
      api<Job>(`/jobs/${id}`)
        .then((j) => {
          if (cancelled) return;
          setReportJob(j);
          if (j.status === "done" && j.result) {
            setReport(j.result);
            notify("리포트가 완성되었습니다.");
          }
          if (j.status === "failed") setError(j.message);
        })
        .catch((e) => {
          if (!cancelled) {
            setError(e.message);
            setReportJob(undefined);
          }
        });
    }, 1200);
    return () => {
      cancelled = true;
      clearInterval(t);
    };
  }, [reportJob]);

  function navigate(id: string) {
    setPage(id);
    setMobileNav(false);
  }
  async function createProject() {
    try {
      const p = await post<Project>("/projects", {
        name: projectName || "새 보컬 프로젝트",
      });
      setProjects((old) => [p, ...old]);
      setPid(p.id);
      setProjectName("");
      setModal("");
      setPage("workspace");
      notify("프로젝트를 만들었습니다.");
    } catch (e) {
      setError((e as Error).message);
    }
  }
  async function uploadFiles(files: FileList | File[] | null) {
    if (!files?.length) return;
    setUploading(true);
    setDragging(false);
    try {
      let target = pid;
      if (!project) {
        const p = await post<Project>("/projects", {
          name: "새 보컬 프로젝트",
        });
        target = p.id;
        setProjects((old) => [p, ...old]);
        setPid(target);
      }
      const count = project?.tracks.length || 0;
      if (files.length + count > 3)
        throw new Error(
          "프로젝트당 최대 3개입니다. 빈 슬롯 수에 맞게 파일을 선택하세요.",
        );
      for (const f of Array.from(files)) {
        const body = new FormData();
        body.append("file", f);
        await api(`/projects/${target}/tracks`, { method: "POST", body });
      }
      await refresh();
      setPage("workspace");
      notify("업로드 완료. 백그라운드에서 보컬을 분석합니다.");
    } catch (e) {
      setError((e as Error).message);
      await refresh();
    } finally {
      setUploading(false);
      if (input.current) input.current.value = "";
    }
  }
  async function remove() {
    if (!deleteTarget) return;
    try {
      await api(
        `/projects/${deleteTarget.type === "project" ? deleteTarget.id : `${pid}/tracks/${deleteTarget.id}`}`,
        { method: "DELETE" },
      );
      if (deleteTarget.type === "project" && deleteTarget.id === pid)
        setPid("");
      if (deleteTarget.type === "track") {
        setEnd(0);
        setReport(undefined);
      }
      setDeleteTarget(null);
      await refresh();
      notify("파일과 분석 데이터를 삭제했습니다.");
    } catch (e) {
      setError((e as Error).message);
    }
  }
  async function changeOffset(t: Track, e: ChangeEvent<HTMLInputElement>) {
    const value = Number(e.target.value);
    if (!Number.isFinite(value)) return;
    try {
      await api(`/projects/${pid}/tracks/${t.id}`, {
        method: "PATCH",
        body: JSON.stringify({ offset: value }),
      });
      setEnd(0);
      setReport(undefined);
      await refresh();
    } catch (e) {
      setError((e as Error).message);
    }
  }
  async function makeReport(ai: boolean) {
    try {
      const j = await post<Job>(`/projects/${pid}/reports`, {
        start,
        end: Math.min(end, duration),
        ai,
      });
      setReport(undefined);
      setReportJob(j);
      setPage("report");
    } catch (e) {
      setError((e as Error).message);
    }
  }
  async function makeTransform() {
    try {
      await post(`/projects/${pid}/tracks/${transformId}/transform`, {
        semitones,
        speed: transformSpeed,
        normalize: transformNormalize,
      });
      setModal("");
      await refresh();
      notify("변형본을 만들고 다시 분석합니다.");
    } catch (e) {
      setError((e as Error).message);
    }
  }
  async function retry(t: Track) {
    try {
      await post(`/projects/${pid}/tracks/${t.id}/analyze`);
      await refresh();
    } catch (e) {
      setError((e as Error).message);
    }
  }
  function downloadReport() {
    if (!report) return;
    const url = URL.createObjectURL(
      new Blob([JSON.stringify(report, null, 2)], { type: "application/json" }),
    );
    const a = document.createElement("a");
    a.href = url;
    a.download = "mas-report.json";
    a.click();
    URL.revokeObjectURL(url);
  }
  const openSettings = (tab = "display") => {
    setSettingTab(tab);
    setModal("settings");
  };
  const pageLabel = NAV.find((n) => n.id === page)?.label;
  const selectedRange = () => (
    <div className="range-controls">
      <span>
        <Clock3 size={13} /> 비교 구간
      </span>
      <label>
        <span className="sr-only">구간 시작 초</span>
        <input
          type="number"
          min={0}
          max={Math.max(0, end - 0.01)}
          step="0.1"
          value={Number(start.toFixed(2))}
          onChange={(e) => {
            const v = Number(e.target.value);
            setStart(Math.max(0, Math.min(v, end - 0.01)));
            setReport(undefined);
          }}
        />
        초
      </label>
      <span>—</span>
      <label>
        <span className="sr-only">구간 끝 초</span>
        <input
          type="number"
          min={start + 0.01}
          max={duration}
          step="0.1"
          value={Number(end.toFixed(2))}
          onChange={(e) => {
            setEnd(
              Math.max(
                start + 0.01,
                Math.min(duration, Number(e.target.value)),
              ),
            );
            setReport(undefined);
          }}
        />
        초
      </label>
      <button
        className={`chip ${loop ? "on" : ""}`}
        onClick={() => setLoop(!loop)}
        aria-pressed={loop}
      >
        <Repeat2 size={13} /> 반복
      </button>
    </div>
  );
  const legend = (
    <div className="legend">
      {tracks.map((t, i) => (
        <span key={t.id}>
          <i style={{ background: COLORS[i] }} />
          {String.fromCharCode(65 + i)}{" "}
          <span className="legend-name">{t.name}</span>
        </span>
      ))}
    </div>
  );

  return (
    <div className="app-shell">
      <input
        ref={input}
        type="file"
        multiple
        accept=".wav,.mp3,.flac"
        className="sr-only"
        onChange={(e) => void uploadFiles(e.target.files)}
      />
      {mobileNav && (
        <div className="sidebar-overlay" onClick={() => setMobileNav(false)} />
      )}
      <aside
        id="sidebar"
        className={`sidebar glass-strong ${mobileNav ? "mobile-open" : ""}`}
      >
        <div className="sb-top">
          <div className="sb-brand">
            <div className="brand-mark">
              <AudioLines size={25} />
            </div>
            <div className="sb-label">
              <h1>
                MAS<span>Studio</span>
              </h1>
              <p>MUSIC ANALYSIS STUDIO</p>
            </div>
            <button
              className="icon-btn mobile-only"
              onClick={() => setMobileNav(false)}
              aria-label="사이드바 닫기"
            >
              <X size={18} />
            </button>
          </div>
          <div className="sb-user glass-soft">
            <span className="avatar">
              <Headphones size={18} />
            </span>
            <div>
              <small>나만의 보컬 연구실</small>
              <strong>Personal studio</strong>
            </div>
            <span className="status-dot" />
          </div>
          <button
            className="sb-new"
            onClick={() => {
              setProjectName("");
              setModal("new");
            }}
            title="새 프로젝트"
          >
            <Plus size={16} />
            <span className="sb-label">새 프로젝트</span>
          </button>
        </div>
        <nav className="sidebar-nav" aria-label="스튜디오 메뉴">
          <p className="nav-caption sb-label">STUDIO</p>
          {NAV.map((n) => (
            <button
              key={n.id}
              onClick={() => navigate(n.id)}
              title={n.label}
              className={`nav-link ${page === n.id ? "active" : ""}`}
              aria-current={page === n.id ? "page" : undefined}
            >
              <n.icon size={18} />
              <span className="sb-label">{n.label}</span>
              {n.id === "compare" && (
                <span className="nav-badge sb-label">A/B/C</span>
              )}
            </button>
          ))}
        </nav>
        <div className="sb-scroll sb-label">
          {showOverview && (
            <section className="sb-overview">
              <p className="nav-caption">OVERVIEW</p>
              <div className="overview-grid">
                {[
                  ["프로젝트", projects.length],
                  [
                    "보컬 트랙",
                    projects.reduce((n, p) => n + p.tracks.length, 0),
                  ],
                  [
                    "분석 완료",
                    projects
                      .flatMap((p) => p.tracks)
                      .filter((t) => t.status === "ready").length,
                  ],
                  [
                    "처리 중",
                    projects
                      .flatMap((p) => p.tracks)
                      .filter((t) =>
                        ["queued", "processing"].includes(t.status),
                      ).length,
                  ],
                ].map(([label, value]) => (
                  <div key={label} className="glass-soft">
                    <small>{label}</small>
                    <strong>{value}</strong>
                  </div>
                ))}
              </div>
            </section>
          )}
          <section className="recent-projects">
            <div className="section-caption">
              <span>RECENT PROJECTS</span>
              <button
                className="icon-btn"
                onClick={() => navigate("dashboard")}
                aria-label="모든 프로젝트"
              >
                <MoreHorizontal size={15} />
              </button>
            </div>
            {projects.slice(0, 4).map((p) => (
              <button
                key={p.id}
                className={p.id === pid ? "selected" : ""}
                onClick={() => {
                  setPid(p.id);
                  navigate("workspace");
                }}
              >
                <FolderOpen size={14} />
                <span>{p.name}</span>
                <small>{p.tracks.length}</small>
              </button>
            ))}
            {!projects.length && (
              <p className="muted tiny">첫 프로젝트를 만들어보세요.</p>
            )}
          </section>
        </div>
        <div className="sb-bottom">
          <div className="engine-state sb-label">
            <div>
              <Cpu size={14} />
              <span>pYIN · NumPy</span>
              <small>CPU</small>
            </div>
            <div>
              <Database size={14} />
              <span>
                {health?.database === "mongodb"
                  ? "MongoDB · SSH"
                  : "로컬 미리보기"}
              </span>
              <i
                className={`status-dot ${error || health?.database_error ? "amber" : ""}`}
              />
            </div>
          </div>
          <div className="sb-account">
            <span className="sb-label version">
              MAS v0.1 <small>보컬 분석</small>
            </span>
            <button
              className="icon-btn"
              title="테마 설정"
              aria-label="테마 설정"
              onClick={() => setModal("theme")}
            >
              <Palette size={17} />
            </button>
            <button
              className="icon-btn"
              title="설정"
              aria-label="설정"
              onClick={() => openSettings()}
            >
              <Settings size={17} />
            </button>
            <button
              className="icon-btn collapse-btn"
              title="사이드바 접기 / 펼치기"
              aria-label="사이드바 접기 / 펼치기"
              onClick={() => setCollapsed(!collapsed)}
            >
              <ChevronsLeft size={17} />
            </button>
          </div>
        </div>
      </aside>
      <main>
        <header className="topbar">
          <div className="breadcrumb">
            <button
              className="icon-btn mobile-only"
              onClick={() => setMobileNav(true)}
              aria-label="사이드바 열기"
            >
              <Menu size={20} />
            </button>
            <span>Studio</span>
            <span className="slash">/</span>
            <strong>{pageLabel}</strong>
            {project && page !== "dashboard" && (
              <>
                <span className="slash">/</span>
                <span className="breadcrumb-project">{project.name}</span>
              </>
            )}
          </div>
          <div className="top-actions">
            <span className="local-pill">
              <span className="status-dot" />
              {error ? "연결 확인 필요" : loading ? "연결 중" : "Local studio"}
            </span>
            <button
              className="icon-btn"
              aria-label="도움말"
              title="도움말"
              onClick={() => setModal("help")}
            >
              <CircleHelp size={17} />
            </button>
            <button
              className="icon-btn"
              aria-label="오디오 설정"
              title="오디오 설정"
              onClick={() => openSettings("audio")}
            >
              <SlidersHorizontal size={17} />
            </button>
          </div>
        </header>
        <div className="content-scroll">
          {error && (
            <div className="error-banner" role="alert">
              <Activity size={17} />
              <span>{error}</span>
              <button
                className="icon-btn"
                onClick={() => setError("")}
                aria-label="오류 닫기"
              >
                <X size={16} />
              </button>
            </div>
          )}
          {page === "dashboard" ? (
            <>
              <div className="page-heading">
                <div>
                  <p className="eyebrow">YOUR CREATIVE SPACE</p>
                  <h2>
                    나의 프로젝트
                    <span className="count">{projects.length}</span>
                  </h2>
                  <p>
                    녹음을 모으고, 차이를 발견하고, 다음 테이크를 준비하세요.
                  </p>
                </div>
                <button className="primary" onClick={() => setModal("new")}>
                  <Plus size={16} /> 새 프로젝트
                </button>
              </div>
              <div className="project-tools">
                <label className="search">
                  <Search size={16} />
                  <input
                    placeholder="프로젝트 검색"
                    value={search}
                    onChange={(e) => setSearch(e.target.value)}
                  />
                </label>
                <span className="muted tiny">최근 수정순</span>
              </div>
              <div className="project-grid">
                {projects
                  .filter((p) =>
                    p.name.toLowerCase().includes(search.toLowerCase()),
                  )
                  .map((p) => (
                    <article className="project-card glass" key={p.id}>
                      <div className="project-card-top">
                        <span className="project-icon">
                          <FolderOpen size={22} />
                        </span>
                        <button
                          className="icon-btn danger"
                          aria-label={`${p.name} 삭제`}
                          onClick={() =>
                            setDeleteTarget({
                              type: "project",
                              id: p.id,
                              name: p.name,
                            })
                          }
                        >
                          <Trash2 size={15} />
                        </button>
                      </div>
                      <button
                        className="project-open"
                        onClick={() => {
                          setPid(p.id);
                          navigate("workspace");
                        }}
                      >
                        <h3>{p.name}</h3>
                        <p>
                          보컬 {p.tracks.length}개 · 분석{" "}
                          {p.tracks.filter((t) => t.status === "ready").length}
                          개 완료
                        </p>
                        <div className="project-slots">
                          {[0, 1, 2].map((i) => (
                            <span
                              key={i}
                              style={{
                                color: COLORS[i],
                                opacity: p.tracks[i] ? 1 : 0.25,
                              }}
                            >
                              <AudioLines size={19} />
                            </span>
                          ))}
                        </div>
                        <small>
                          {new Date(p.updated_at).toLocaleString("ko-KR")}
                        </small>
                      </button>
                    </article>
                  ))}
                <button
                  className="new-project-card"
                  onClick={() => setModal("new")}
                >
                  <Plus size={24} />
                  <span>새로운 비교 시작하기</span>
                  <small>하나의 프로젝트, 최대 3개의 보컬</small>
                </button>
              </div>
              <div className="info-strip">
                <Database size={17} />
                <p>
                  {health?.database === "mongodb"
                    ? "프로젝트 메타데이터는 MongoDB에, 오디오는 로컬에 저장됩니다."
                    : "현재 로컬 미리보기 모드입니다. MongoDB SSH 연결은 설정에서 확인하세요."}
                </p>
                <button
                  className="text-button"
                  onClick={() => openSettings("database")}
                >
                  연결 설정
                </button>
              </div>
            </>
          ) : (
            <>
              <div className="page-heading">
                <div>
                  <p className="eyebrow">
                    VOCAL COMPARISON STUDIO <span>보컬 전용</span>
                  </p>
                  <div className="title-row">
                    <h2>{project?.name || "보컬의 차이를, 더 선명하게."}</h2>
                    {project && (
                      <button
                        className="icon-btn"
                        aria-label="프로젝트 이름 변경"
                        title="프로젝트 이름 변경"
                        onClick={() => {
                          setProjectName(project.name);
                          setModal("rename");
                        }}
                      >
                        <MoreHorizontal size={19} />
                      </button>
                    )}
                  </div>
                  <p>
                    {page === "workspace"
                      ? "같은 구간을 듣고, 측정하고, 나만의 보컬을 이해하세요."
                      : page === "compare"
                        ? "최대 3개의 보컬을 같은 타임라인에서 비교하세요."
                        : page === "analysis"
                          ? "실제 오디오에서 추출한 음정, 다이내믹, 주파수 특성입니다."
                          : "측정된 데이터에서 출발하는 보컬 피드백."}
                  </p>
                </div>
                <div className="heading-actions">
                  {project && (
                    <button
                      className="secondary"
                      onClick={() => navigate("dashboard")}
                    >
                      <FolderOpen size={15} />
                      <span>프로젝트</span>
                      <ChevronDown size={12} />
                    </button>
                  )}
                  <button
                    className="primary"
                    disabled={uploading || tracks.length >= 3}
                    onClick={() => input.current?.click()}
                  >
                    {uploading ? (
                      <LoaderCircle size={16} className="spin" />
                    ) : (
                      <Upload size={16} />
                    )}{" "}
                    보컬 추가
                  </button>
                </div>
              </div>
              {page === "workspace" && (
                <>
                  <div
                    className={`upload-zone ${dragging ? "dragging" : ""} ${tracks.length ? "compact" : ""}`}
                    onDragOver={(e) => {
                      e.preventDefault();
                      setDragging(true);
                    }}
                    onDragLeave={() => setDragging(false)}
                    onDrop={(e) => {
                      e.preventDefault();
                      void uploadFiles(e.dataTransfer.files);
                    }}
                  >
                    <div className="upload-symbol">
                      <Upload size={22} />
                    </div>
                    <div>
                      <h3>
                        {tracks.length
                          ? "비교할 보컬을 더 추가하세요"
                          : "보컬 파일을 여기에 놓아주세요"}
                      </h3>
                      <p>
                        WAV, MP3, FLAC · 최대 {health?.max_upload_mb || 150}MB /{" "}
                        {Math.round((health?.max_duration_seconds || 300) / 60)}
                        분 · 프로젝트당 3개
                      </p>
                    </div>
                    <button
                      className="secondary"
                      disabled={uploading || tracks.length >= 3}
                      onClick={() => input.current?.click()}
                    >
                      파일 선택 <Plus size={14} />
                    </button>
                    <span className="upload-note">
                      분리된 보컬 파일을 권장합니다. 음원 분리 기능은
                      별도입니다.
                    </span>
                  </div>
                </>
              )}
              {page !== "report" && (
                <>
                  <section className="panel glass track-panel">
                    <div className="panel-head">
                      <h3>
                        <AudioLines size={17} />
                        {page === "compare"
                          ? "A / B / C 비교 플레이어"
                          : "보컬 워크스페이스"}
                        <span className="small-badge">{tracks.length} / 3</span>
                      </h3>
                      <div className="panel-actions">
                        <button
                          className={`chip ${normalize ? "on" : ""}`}
                          onClick={() => setNormalize(!normalize)}
                          aria-pressed={normalize}
                        >
                          <Gauge size={13} />
                          <span>음량 매칭</span>
                        </button>
                        <button
                          className={`chip ${multi ? "on" : ""}`}
                          onClick={() => setMulti(!multi)}
                          aria-pressed={multi}
                          title="끄면 A/B/C 선택 트랙만 들립니다"
                        >
                          <Headphones size={13} />
                          {multi ? "동시 재생" : "A/B/C 전환"}
                        </button>
                      </div>
                    </div>
                    {tracks.length ? (
                      <>
                        <div className="time-ruler">
                          <span>TRACK / VOCAL</span>
                          <div>
                            <span>00:00</span>
                            <span>{fmt(duration / 4)}</span>
                            <span>{fmt(duration / 2)}</span>
                            <span>{fmt((duration * 3) / 4)}</span>
                            <span>{fmt(duration)}</span>
                          </div>
                        </div>
                        <div className="track-list">
                          {tracks.map((t, i) => {
                            const a = analyses[t.id];
                            const m = mixFor(t.id);
                            const j = t.job_id ? jobs[t.job_id] : undefined;
                            return (
                              <article
                                className={`track-row ${active?.id === t.id ? "selected" : ""}`}
                                style={
                                  {
                                    "--track-color": COLORS[i],
                                  } as React.CSSProperties
                                }
                                key={t.id}
                              >
                                <div className="track-info">
                                  <div className="track-title">
                                    <button
                                      className="track-letter"
                                      onClick={() => setSelected(t.id)}
                                      aria-label={`트랙 ${String.fromCharCode(65 + i)} 선택`}
                                      aria-pressed={active?.id === t.id}
                                    >
                                      {String.fromCharCode(65 + i)}
                                    </button>
                                    <div>
                                      <strong title={t.name}>{t.name}</strong>
                                      <small>
                                        {t.status === "ready"
                                          ? `${fmt(t.duration)} · MONO · 22.05 kHz${t.cache_hit ? " · 캐시" : ""}`
                                          : t.status === "failed"
                                            ? "처리 실패"
                                            : j?.message || "분석 대기 중"}
                                      </small>
                                    </div>
                                  </div>
                                  <div className="track-controls">
                                    <button
                                      className={m.mute ? "on" : ""}
                                      onClick={() =>
                                        setTrackMix(t.id, { mute: !m.mute })
                                      }
                                      aria-pressed={m.mute}
                                      title="Mute"
                                      aria-label={`${t.name} 음소거`}
                                    >
                                      M
                                    </button>
                                    <button
                                      className={m.solo ? "on solo" : ""}
                                      onClick={() =>
                                        setTrackMix(t.id, { solo: !m.solo })
                                      }
                                      aria-pressed={m.solo}
                                      title="Solo"
                                      aria-label={`${t.name} 솔로`}
                                    >
                                      S
                                    </button>
                                    <Volume2 size={12} />
                                    <input
                                      aria-label={`${t.name} 음량`}
                                      type="range"
                                      min="0"
                                      max="1"
                                      step="0.01"
                                      value={m.volume}
                                      onChange={(e) =>
                                        setTrackMix(t.id, {
                                          volume: Number(e.target.value),
                                        })
                                      }
                                    />
                                    <span>{Math.round(m.volume * 100)}%</span>
                                  </div>
                                </div>
                                <div className="track-wave">
                                  <Waveform
                                    analysis={a}
                                    color={COLORS[i]}
                                    position={position}
                                    offset={t.offset}
                                    duration={t.duration}
                                    onSeek={seek}
                                  />
                                  {t.status === "processing" ||
                                  t.status === "queued" ? (
                                    <div className="track-progress">
                                      <span
                                        style={{
                                          width: `${j?.progress || 3}%`,
                                        }}
                                      />
                                      <small>
                                        <LoaderCircle
                                          size={11}
                                          className="spin"
                                        />
                                        {j?.progress || 0}%
                                      </small>
                                    </div>
                                  ) : null}
                                  {t.error && (
                                    <div className="track-error">
                                      {t.error}
                                      <button
                                        className="text-button"
                                        onClick={() => void retry(t)}
                                      >
                                        재시도
                                      </button>
                                    </div>
                                  )}
                                </div>
                                <div className="track-end">
                                  <label className="offset-control">
                                    오프셋
                                    <input
                                      aria-label={`${t.name} 시간 오프셋 초`}
                                      key={`${t.id}-${t.offset}`}
                                      type="number"
                                      defaultValue={t.offset}
                                      step="0.1"
                                      min="-120"
                                      max="120"
                                      onBlur={(e) => void changeOffset(t, e)}
                                    />
                                    s
                                  </label>
                                  <div>
                                    <button
                                      className="icon-btn"
                                      disabled={
                                        t.status !== "ready" ||
                                        tracks.length >= 3
                                      }
                                      aria-label={`${t.name} 변형`}
                                      title="키 · 속도 변형본 만들기"
                                      onClick={() => {
                                        setTransformId(t.id);
                                        setSemitones(0);
                                        setTransformSpeed(1);
                                        setModal("transform");
                                      }}
                                    >
                                      <SlidersHorizontal size={14} />
                                    </button>
                                    {t.status === "ready" && (
                                      <a
                                        className="icon-btn"
                                        href={trackUrl(pid, t.id)}
                                        aria-label={`${t.name} WAV 다운로드`}
                                        title="분석용 WAV 다운로드"
                                      >
                                        <ArrowDownToLine size={14} />
                                      </a>
                                    )}
                                    <button
                                      className="icon-btn danger"
                                      disabled={busy}
                                      aria-label={`${t.name} 삭제`}
                                      title="트랙 삭제"
                                      onClick={() =>
                                        setDeleteTarget({
                                          type: "track",
                                          id: t.id,
                                          name: t.name,
                                        })
                                      }
                                    >
                                      <Trash2 size={14} />
                                    </button>
                                  </div>
                                </div>
                              </article>
                            );
                          })}
                        </div>
                        {tracks.length < 3 && (
                          <button
                            className="add-track"
                            disabled={uploading}
                            onClick={() => input.current?.click()}
                          >
                            <Plus size={14} /> 보컬{" "}
                            {String.fromCharCode(65 + tracks.length)} 추가
                            <span>비교할 녹음을 불러오세요</span>
                          </button>
                        )}
                      </>
                    ) : (
                      <Empty
                        title="첫 번째 보컬을 기다리고 있어요"
                        description="원곡의 보컬, 커버, 연습 녹음. 비교하고 싶은 파일을 추가하세요."
                      >
                        <div className="empty-slots">
                          {["A", "B", "C"].map((l, i) => (
                            <span key={l} style={{ color: COLORS[i] }}>
                              {l}
                            </span>
                          ))}
                        </div>
                      </Empty>
                    )}
                    <div className="transport">
                      <div className="transport-buttons">
                        <button
                          className="icon-btn"
                          onClick={() => {
                            stop();
                            seek(0);
                          }}
                          disabled={!ready.length}
                          aria-label="정지 및 처음으로"
                        >
                          <Square size={15} />
                        </button>
                        <button
                          className="play-button"
                          disabled={!ready.length}
                          onClick={() => {
                            if (playing) stop();
                            else {
                              if (
                                position >= duration ||
                                (loop && (position < start || position >= end))
                              )
                                seek(loop ? start : 0);
                              setPlaying(true);
                            }
                          }}
                          aria-label={playing ? "일시정지" : "재생"}
                        >
                          {playing ? (
                            <Pause size={18} fill="currentColor" />
                          ) : (
                            <Play size={18} fill="currentColor" />
                          )}
                        </button>
                        <button
                          className={`icon-btn ${loop ? "active" : ""}`}
                          disabled={!ready.length}
                          onClick={() => setLoop(!loop)}
                          aria-label="구간 반복"
                          aria-pressed={loop}
                        >
                          <Repeat2 size={17} />
                        </button>
                      </div>
                      <div className="transport-time">
                        <strong>
                          {fmt(position)}
                          <span>
                            .
                            {Math.floor((position % 1) * 100)
                              .toString()
                              .padStart(2, "0")}
                          </span>
                        </strong>
                        <small>/ {fmt(duration)}</small>
                      </div>
                      <input
                        className="transport-seek"
                        aria-label="전체 재생 위치"
                        type="range"
                        min={0}
                        max={duration || 1}
                        step="0.01"
                        value={Math.min(position, duration)}
                        disabled={!duration}
                        onChange={(e) => seek(Number(e.target.value))}
                      />
                      <label className="speed-label">
                        <span>속도</span>
                        <select
                          aria-label="재생 속도"
                          value={speed}
                          onChange={(e) => setSpeed(Number(e.target.value))}
                        >
                          {[0.5, 0.75, 1, 1.25, 1.5, 2].map((v) => (
                            <option key={v} value={v}>
                              {v.toFixed(2)}×
                            </option>
                          ))}
                        </select>
                      </label>
                      <div className="ab-switch" aria-label="청취 트랙">
                        {tracks.map((t, i) => (
                          <button
                            key={t.id}
                            disabled={t.status !== "ready"}
                            className={active?.id === t.id ? "on" : ""}
                            onClick={() => {
                              setSelected(t.id);
                              setMulti(false);
                            }}
                            style={
                              {
                                "--track-color": COLORS[i],
                              } as React.CSSProperties
                            }
                            aria-pressed={active?.id === t.id}
                          >
                            {String.fromCharCode(65 + i)}
                          </button>
                        ))}
                      </div>
                    </div>
                  </section>
                  <div className="analysis-heading">
                    <h3>
                      보컬 인사이트 <span>실제 측정 데이터</span>
                    </h3>
                    <div className="active-select">
                      <span>기준 보컬</span>
                      <select
                        value={active?.id || ""}
                        aria-label="분석 기준 보컬"
                        onChange={(e) => setSelected(e.target.value)}
                      >
                        {!tracks.length && (
                          <option value="">선택할 트랙 없음</option>
                        )}
                        {tracks.map((t, i) => (
                          <option key={t.id} value={t.id}>
                            {String.fromCharCode(65 + i)} · {t.name}
                          </option>
                        ))}
                      </select>
                    </div>
                  </div>
                  <div className="metric-grid">
                    {[
                      {
                        label: "추정 음역",
                        icon: Mic2,
                        value: activeSummary?.pitch_low_note
                          ? `${activeSummary.pitch_low_note} – ${activeSummary.pitch_high_note}`
                          : "—",
                        unit: "",
                        sub: "유효 음정의 5–95 백분위",
                        color: "blue",
                      },
                      {
                        label: "평균 RMS",
                        icon: Activity,
                        value: activeSummary?.rms_db.toFixed(1) ?? "—",
                        unit: "dBFS",
                        sub: "녹음 전체의 신호 에너지",
                        color: "purple",
                      },
                      {
                        label: "다이내믹 범위",
                        icon: Waves,
                        value:
                          activeSummary?.dynamic_range_db.toFixed(1) ?? "—",
                        unit: "dB",
                        sub: "활성 프레임의 P95 − P10",
                        color: "green",
                      },
                      {
                        label: "음정 추적률",
                        icon: Gauge,
                        value: activeSummary?.voiced_percent.toFixed(1) ?? "—",
                        unit: "%",
                        sub: "전체 중 유효 유성음 프레임",
                        color: "amber",
                      },
                    ].map((m) => (
                      <article
                        className={`metric glass ${m.color}`}
                        key={m.label}
                      >
                        <div>
                          <span>{m.label}</span>
                          <m.icon size={16} />
                        </div>
                        <strong>
                          {m.value}
                          <small>{m.unit}</small>
                        </strong>
                        <p>{m.sub}</p>
                      </article>
                    ))}
                  </div>
                  <div className="insight-grid">
                    <section className="panel glass chart-panel">
                      <div className="panel-head">
                        <h3>
                          <Activity size={16} />
                          {chartKind === "pitch"
                            ? "음정 윤곽"
                            : chartKind === "rms"
                              ? "다이내믹"
                              : "스펙트럼 중심"}
                          <span className="small-badge">
                            {chartKind === "pitch"
                              ? "pYIN"
                              : chartKind === "rms"
                                ? "RMS"
                                : "STFT"}
                          </span>
                        </h3>
                        <div className="segmented">
                          {[
                            ["pitch", "음정"],
                            ["rms", "RMS"],
                            ["centroid", "음색"],
                          ].map(([id, label]) => (
                            <button
                              key={id}
                              onClick={() => setChartKind(id)}
                              className={chartKind === id ? "on" : ""}
                            >
                              {label}
                            </button>
                          ))}
                        </div>
                      </div>
                      {legend}
                      {ready.length ? (
                        <AnalysisChart
                          tracks={tracks}
                          analyses={analyses}
                          kind={chartKind}
                          start={start}
                          end={end || duration}
                        />
                      ) : (
                        <div className="chart-empty">
                          <BarChart3 size={25} />
                          <p>보컬을 분석하면 비교 그래프가 나타납니다.</p>
                          <span>음정 · 다이내믹 · 스펙트럼 중심</span>
                        </div>
                      )}
                      <div className="chart-footer">
                        {selectedRange()}
                        <span className="tiny muted">
                          오프셋 적용 · 시간 기준 비교
                        </span>
                      </div>
                    </section>
                    <section className="panel glass ai-preview">
                      <div className="panel-head">
                        <h3>
                          <Sparkles size={17} /> AI 보컬 코치
                        </h3>
                        <span
                          className={`small-badge ${health?.ai_configured ? "green" : ""}`}
                        >
                          {health?.ai_configured ? "연결됨" : "연결 대기"}
                        </span>
                      </div>
                      <div className="coach-orb">
                        <Sparkles size={26} />
                      </div>
                      <h3>수치를 이해로 바꾸는 피드백</h3>
                      <p>
                        선택한 구간의 측정값을 바탕으로
                        <br />
                        보컬의 차이와 연습 방향을 살펴보세요.
                      </p>
                      <div className="coach-points">
                        <span>
                          <Check size={12} /> 수치와 구간을 함께 설명
                        </span>
                        <span>
                          <Check size={12} /> 해석의 근거와 한계 표시
                        </span>
                      </div>
                      <button
                        className="primary"
                        disabled={!ready.length || !health?.ai_configured}
                        onClick={() => void makeReport(true)}
                      >
                        <Sparkles size={14} /> AI 평가 생성
                      </button>
                      {!health?.ai_configured && (
                        <button
                          className="text-button"
                          onClick={() => openSettings("ai")}
                        >
                          AI 연결 설정 <ArrowLeftRight size={12} />
                        </button>
                      )}
                      <button
                        className="text-button"
                        disabled={!ready.length}
                        onClick={() => void makeReport(false)}
                      >
                        측정 리포트 보기
                      </button>
                    </section>
                  </div>
                  {(page === "analysis" || page === "compare") && (
                    <section className="panel glass detail-panel">
                      <div className="panel-head">
                        <h3>
                          <Waves size={17} /> 스펙트로그램
                          <span className="small-badge">
                            선택 보컬 · 전체 음원
                          </span>
                        </h3>
                        <span className="tiny muted">
                          로그 주파수 · STFT 2048
                        </span>
                      </div>
                      {activeAnalysis ? (
                        <Spectrogram analysis={activeAnalysis} />
                      ) : (
                        <div className="chart-empty">
                          분석이 완료된 보컬을 선택하세요.
                        </div>
                      )}
                      {activeAnalysis && (
                        <div className="vibrato-list">
                          <h4>
                            비브라토 후보{" "}
                            <span className="small-badge">
                              {activeAnalysis.vibrato_candidates.length}개
                            </span>
                          </h4>
                          {activeAnalysis.vibrato_candidates.length ? (
                            activeAnalysis.vibrato_candidates
                              .slice(0, 8)
                              .map((v, i) => (
                                <button
                                  key={i}
                                  className="vibrato-item"
                                  onClick={() => {
                                    setStart(
                                      Math.max(
                                        0,
                                        v.start - (active?.offset || 0),
                                      ),
                                    );
                                    setEnd(
                                      Math.max(
                                        0.01,
                                        v.end - (active?.offset || 0),
                                      ),
                                    );
                                    seek(v.start - (active?.offset || 0));
                                    setLoop(true);
                                  }}
                                >
                                  <span>
                                    {v.start.toFixed(1)}–{v.end.toFixed(1)}초{" "}
                                    <small>원본 기준</small>
                                  </span>
                                  <span>
                                    {v.rate_hz} Hz · 폭 {v.width_cents} cent
                                  </span>
                                  <Play size={12} />
                                </button>
                              ))
                          ) : (
                            <p className="muted tiny">
                              기준에 맞는 주기적 변화가 발견되지 않았습니다.
                              창법에 대한 판단은 아닙니다.
                            </p>
                          )}
                        </div>
                      )}
                    </section>
                  )}
                  {activeAnalysis && (
                    <div className="analysis-limit">
                      <CircleHelp size={15} />
                      <div>
                        {activeAnalysis.warnings.map((w, i) => (
                          <p key={i}>{w}</p>
                        ))}
                      </div>
                    </div>
                  )}
                  {!tracks.length && (
                    <div className="getting-started">
                      <div>
                        <span>01</span>
                        <strong>보컬을 불러오세요</strong>
                        <p>분리된 보컬 또는 연습 녹음</p>
                      </div>
                      <div>
                        <span>02</span>
                        <strong>같은 구간을 비교하세요</strong>
                        <p>A/B/C 전환 · 음량 매칭 · 반복</p>
                      </div>
                      <div>
                        <span>03</span>
                        <strong>다음 테이크를 준비하세요</strong>
                        <p>실제 그래프와 근거 있는 피드백</p>
                      </div>
                    </div>
                  )}
                </>
              )}
              {page === "report" && (
                <>
                  {!!project?.reports?.length && (
                    <div className="report-history">
                      <label>
                        저장된 리포트{" "}
                        <select
                          aria-label="저장된 리포트"
                          value=""
                          onChange={(e) => {
                            if (e.target.value !== "") {
                              setReport(
                                project.reports![Number(e.target.value)],
                              );
                              setReportJob(undefined);
                            }
                          }}
                        >
                          <option value="">
                            이전 리포트 열기 ({project.reports.length})
                          </option>
                          {project.reports.map((r, i) => (
                            <option key={i} value={i}>
                              {r.kind === "ai" ? "AI 평가" : "측정 리포트"} ·{" "}
                              {new Date(r.created_at).toLocaleString("ko-KR")}
                            </option>
                          ))}
                        </select>
                      </label>
                    </div>
                  )}
                  <section className="panel glass report-panel">
                    <div className="panel-head">
                      <h3>
                        <Sparkles size={17} /> 보컬 평가 리포트
                      </h3>
                      {report && (
                        <button className="secondary" onClick={downloadReport}>
                          <ArrowDownToLine size={14} /> JSON 내보내기
                        </button>
                      )}
                    </div>
                    <div className="report-toolbar">
                      {selectedRange()}
                      <button
                        className="secondary"
                        disabled={
                          !ready.length ||
                          !!(
                            reportJob &&
                            !["done", "failed"].includes(reportJob.status)
                          )
                        }
                        onClick={() => void makeReport(false)}
                      >
                        측정 리포트
                      </button>
                      <button
                        className="primary"
                        disabled={
                          !ready.length ||
                          !health?.ai_configured ||
                          !!(
                            reportJob &&
                            !["done", "failed"].includes(reportJob.status)
                          )
                        }
                        onClick={() => void makeReport(true)}
                      >
                        <Sparkles size={14} /> AI 평가 생성
                      </button>
                    </div>
                    {reportJob &&
                    !["done", "failed"].includes(reportJob.status) ? (
                      <div className="report-wait">
                        <LoaderCircle size={30} className="spin" />
                        <h3>{reportJob.message}</h3>
                        <p>측정 데이터를 해석하고 있습니다.</p>
                      </div>
                    ) : report ? (
                      <div className="report-content">
                        <div className="report-meta">
                          <span className="small-badge">
                            {report.kind === "ai"
                              ? `AI 해석 · ${report.model}`
                              : "측정 기반 · AI 미사용"}
                          </span>
                          <span>
                            {new Date(report.created_at).toLocaleString(
                              "ko-KR",
                            )}
                          </span>
                        </div>
                        <pre>{report.content}</pre>
                        <details>
                          <summary>분석 근거 JSON</summary>
                          <pre>{JSON.stringify(report.evidence, null, 2)}</pre>
                        </details>
                      </div>
                    ) : (
                      <Empty
                        title="근거가 있는 피드백, 여기에서 시작해요"
                        description="보컬을 분석한 뒤 비교 구간을 지정하고 리포트를 생성하세요."
                      />
                    )}
                    <div className="report-disclosure">
                      <CircleHelp size={14} />
                      <p>
                        AI 평가를 누르면 선택 구간의 측정 요약이 설정한 제공자로
                        전송됩니다. 오디오 파일은 전송하지 않습니다. AI 미연결
                        상태에서도 측정 리포트를 사용할 수 있습니다.
                      </p>
                      {!health?.ai_configured && (
                        <button
                          className="text-button"
                          onClick={() => openSettings("ai")}
                        >
                          AI 연결 설정
                        </button>
                      )}
                    </div>
                  </section>
                </>
              )}
            </>
          )}
          <footer className="page-footer">
            <span>
              <AudioLines size={12} /> MUSIC ANALYSIS STUDIO
            </span>
            <span>들리는 차이를, 이해할 수 있는 데이터로.</span>
            <small>v0.1 · Vocal only</small>
          </footer>
        </div>
        <nav className="mobile-tabs" aria-label="모바일 하단 메뉴">
          {NAV.map((n) => (
            <button
              key={n.id}
              className={page === n.id ? "on" : ""}
              onClick={() => navigate(n.id)}
            >
              <n.icon size={18} />
              <span>{n.label}</span>
            </button>
          ))}
        </nav>
      </main>
      {toast && (
        <div className="toast" role="status">
          <Check size={16} />
          {toast}
        </div>
      )}
      {(modal === "new" || modal === "rename") && (
        <Modal
          title={modal === "new" ? "새 프로젝트" : "이름 변경"}
          onClose={closeModal}
        >
          <ModalHead
            title={modal === "new" ? "새 보컬 프로젝트" : "프로젝트 이름 변경"}
            subtitle="하나의 프로젝트에서 최대 3개의 보컬을 비교합니다."
            onClose={closeModal}
          />
          <form
            className="modal-form"
            onSubmit={(e) => {
              e.preventDefault();
              if (modal === "new") void createProject();
              else
                void api(`/projects/${pid}`, {
                  method: "PATCH",
                  body: JSON.stringify({ name: projectName }),
                })
                  .then(() => {
                    setModal("");
                    void refresh();
                  })
                  .catch((e) => setError(e.message));
            }}
          >
            <label>
              프로젝트 이름
              <input
                autoFocus
                placeholder="예: 후렴 보컬 · Take 01 vs Take 02"
                maxLength={120}
                value={projectName}
                onChange={(e) => setProjectName(e.target.value)}
              />
            </label>
            <div className="form-actions">
              <button type="button" className="secondary" onClick={closeModal}>
                취소
              </button>
              <button className="primary" type="submit">
                {modal === "new" ? "프로젝트 만들기" : "저장"}
              </button>
            </div>
          </form>
        </Modal>
      )}
      {modal === "theme" && (
        <Modal title="테마 설정" onClose={closeModal}>
          <ModalHead
            title="테마 설정"
            subtitle="UnivDash의 네 가지 테마를 그대로."
            onClose={closeModal}
          />
          <ThemePicker />
        </Modal>
      )}
      {modal === "settings" && (
        <Modal title="설정" onClose={closeModal} wide>
          <div className="settings-layout">
            <aside className="settings-nav">
              <h3>
                <Settings size={17} /> 설정
              </h3>
              <div>
                {SETTINGS.map((t) => (
                  <button
                    key={t.id}
                    className={settingTab === t.id ? "on" : ""}
                    onClick={() => setSettingTab(t.id)}
                  >
                    <t.icon size={17} />
                    <span>
                      <strong>{t.label}</strong>
                      <small>{t.desc}</small>
                    </span>
                  </button>
                ))}
              </div>
              <span className="settings-version">
                Music Analysis Studio · v0.1
              </span>
            </aside>
            <section className="settings-pane">
              <ModalHead
                title={SETTINGS.find((t) => t.id === settingTab)?.label || ""}
                onClose={closeModal}
              />
              <div className="settings-body">
                {settingTab === "display" && (
                  <>
                    <p className="settings-note">
                      이 기기에 저장되며 바로 적용됩니다.
                    </p>
                    <ThemePicker />
                    <SettingRow
                      title="사이드바 접기"
                      description="아이콘만 표시해 작업 공간을 넓힙니다."
                    >
                      <Toggle
                        value={collapsed}
                        onChange={() => setCollapsed(!collapsed)}
                        label="사이드바 접기"
                      />
                    </SettingRow>
                    <SettingRow
                      title="Overview 표시"
                      description="프로젝트와 분석 상태 요약"
                    >
                      <Toggle
                        value={showOverview}
                        onChange={() => setShowOverview(!showOverview)}
                        label="Overview 표시"
                      />
                    </SettingRow>
                  </>
                )}
                {settingTab === "audio" && (
                  <>
                    <p className="settings-note">
                      현재 프로젝트의 모든 보컬은 하나의 타임라인을 공유합니다.
                    </p>
                    <SettingRow
                      title="재생 음량 매칭"
                      description="전체 RMS가 가장 낮은 트랙에 맞춰 감쇠합니다. 원본 파일에는 영향을 주지 않습니다. LUFS 정규화는 아닙니다."
                    >
                      <Toggle
                        value={normalize}
                        onChange={() => setNormalize(!normalize)}
                        label="음량 매칭"
                      />
                    </SettingRow>
                    <SettingRow
                      title="동시 재생"
                      description="끄면 A/B/C 중 선택한 트랙만 들립니다. Solo가 선택되어 있으면 Solo가 우선합니다."
                    >
                      <Toggle
                        value={multi}
                        onChange={() => setMulti(!multi)}
                        label="동시 재생"
                      />
                    </SettingRow>
                    <SettingRow
                      title="분석용 재생 파일"
                      description="모노 · 22,050 Hz · 32-bit float WAV. 업로드 원본은 별도로 보관합니다."
                    >
                      <span className="small-badge">WAV</span>
                    </SettingRow>
                    <SettingRow
                      title="키와 속도"
                      description="재생 속도는 음높이를 유지합니다. 트랙의 슬라이더 버튼에서 키와 속도를 독립적으로 변형한 복사본을 만들 수 있습니다."
                    />
                  </>
                )}
                {settingTab === "analysis" && (
                  <>
                    <div className="settings-status">
                      <Cpu size={24} />
                      <div>
                        <strong>NumPy + librosa pYIN</strong>
                        <p>실제 PCM 신호를 분석하는 CPU 엔진</p>
                      </div>
                      <span className="small-badge green">사용 가능</span>
                    </div>
                    <SettingRow
                      title="음정 추적"
                      description="pYIN · 65.4–1568 Hz · 2048 frame / 512 hop. 낮은 확률과 무음 프레임은 제외합니다."
                    />
                    <SettingRow
                      title="시각화"
                      description="음정, RMS, 스펙트럼 중심, 로그 주파수 스펙트로그램, 비브라토 후보"
                    />
                    <SettingRow
                      title="처리 장치"
                      description="현재 분석 엔진은 CPU를 사용합니다. GPU 신경망 모델과 보컬 분리는 이번 버전에 포함되지 않습니다."
                    >
                      <span className="small-badge">CPU</span>
                    </SettingRow>
                    <SettingRow
                      title="수치의 의미"
                      description="음 중심 편차는 음정 정확도 점수가 아닙니다. 기식성·성구·창법은 확정 분류하지 않습니다."
                    />
                  </>
                )}
                {settingTab === "database" && (
                  <>
                    <div className="settings-status">
                      <Database size={24} />
                      <div>
                        <strong>
                          {health?.database === "mongodb"
                            ? "MongoDB"
                            : "로컬 미리보기"}
                        </strong>
                        <p>
                          {health?.database_error ||
                            (health?.database === "mongodb"
                              ? "프로젝트 메타데이터 저장소"
                              : "SSH / MongoDB 접속 정보 입력 전 JSON 저장")}
                        </p>
                      </div>
                      <span className="small-badge">
                        {health?.ssh_enabled ? "SSH" : "LOCAL"}
                      </span>
                    </div>
                    <SettingRow
                      title="MongoDB 설정"
                      description=".env에 MAS_MONGO_URI와 MAS_MONGO_DATABASE를 입력하세요. SSH 별칭을 사용하면 앱 시작 시 터널을 엽니다."
                    />
                    <div className="code-block">
                      MAS_SSH_ALIAS=사용할-별칭
                      <br />
                      MAS_SSH_REMOTE_HOST=127.0.0.1
                      <br />
                      MAS_SSH_REMOTE_PORT=27017
                      <br />
                      MAS_SSH_LOCAL_PORT=27018
                      <br />
                      MAS_MONGO_URI=mongodb://…:27018/…
                      <br />
                      MAS_MONGO_DATABASE=music_analysis_studio
                    </div>
                    <p className="settings-note">
                      비밀번호는 서버 .env에만 보관합니다. 변경 후 서버를 다시
                      시작하세요. 연결 실패 시 오류를 표시하며 로컬 모드로 자동
                      전환하지 않습니다.
                    </p>
                    <SettingRow
                      title="파일 저장 위치"
                      description={health?.storage || "storage/"}
                    >
                      <FolderOpen size={16} />
                    </SettingRow>
                    <SettingRow
                      title="업로드 제한"
                      description={`${health?.max_upload_mb || 150}MB · 최대 ${health?.max_duration_seconds || 300}초 / 파일. 삭제하면 원본·변형본·분석 파일과 사용하지 않는 캐시도 정리합니다.`}
                    />
                  </>
                )}
                {settingTab === "ai" && (
                  <>
                    <div className="settings-status">
                      <Sparkles size={24} />
                      <div>
                        <strong>
                          {health?.ai_configured
                            ? health.ai_model
                            : "AI 제공자 미연결"}
                        </strong>
                        <p>측정 데이터 기반 자연어 평가</p>
                      </div>
                      <span className="small-badge">
                        {health?.ai_configured ? "설정됨" : "설정 필요"}
                      </span>
                    </div>
                    <SettingRow
                      title="지원 연결"
                      description="OpenAI 호환 Chat Completions API의 전체 URL을 지정합니다. 로컬 모델 서버도 연결할 수 있습니다."
                    />
                    <div className="code-block">
                      MAS_AI_URL=https://…/v1/chat/completions
                      <br />
                      MAS_AI_MODEL=사용할-모델
                      <br />
                      MAS_AI_API_KEY=서버에서만-설정
                    </div>
                    <SettingRow
                      title="제공자에 전송되는 데이터"
                      description="선택 구간, 수동 오프셋, 음정·RMS 등 측정 요약과 파일명. 오디오 원본은 보내지 않습니다."
                    />
                    <SettingRow
                      title="평가 기준"
                      description="관찰 · 수치 근거 · 연습 제안 · 신뢰도와 한계. 전체 가창 순위나 근거 없는 창법 판정은 하지 않습니다."
                    />
                    <p className="settings-note">
                      설정 후 서버를 다시 시작하세요. API 호출 비용과 데이터
                      처리는 선택한 제공자의 정책을 따릅니다.
                    </p>
                  </>
                )}
              </div>
            </section>
          </div>
        </Modal>
      )}
      {modal === "transform" && (
        <Modal title="보컬 변형" onClose={closeModal}>
          <ModalHead
            title="키 · 속도 변형본 만들기"
            subtitle="원본을 보존하고, 변형된 오디오를 새 트랙으로 추가해 다시 분석합니다."
            onClose={closeModal}
          />
          <div className="modal-form">
            <label>
              키 변경{" "}
              <strong>
                {semitones > 0 ? "+" : ""}
                {semitones} 반음
              </strong>
              <input
                type="range"
                min="-12"
                max="12"
                step="1"
                value={semitones}
                onChange={(e) => setSemitones(Number(e.target.value))}
              />
            </label>
            <label>
              속도 <strong>{transformSpeed.toFixed(2)}×</strong>
              <input
                type="range"
                min="0.5"
                max="2"
                step="0.05"
                value={transformSpeed}
                onChange={(e) => setTransformSpeed(Number(e.target.value))}
              />
            </label>
            <SettingRow
              title="피크 정규화"
              description="변형본의 피크를 0.95로 맞춥니다."
            >
              <Toggle
                value={transformNormalize}
                onChange={() => setTransformNormalize(!transformNormalize)}
                label="변형본 피크 정규화"
              />
            </SettingRow>
            <p className="muted tiny">
              librosa 위상 보코더를 사용하며 변형 과정에서 음색에 아티팩트가
              생길 수 있습니다.
            </p>
            <button className="primary" onClick={() => void makeTransform()}>
              <SlidersHorizontal size={15} /> 변형하고 분석하기
            </button>
          </div>
        </Modal>
      )}
      {modal === "help" && (
        <Modal title="사용 안내" onClose={closeModal}>
          <ModalHead title="보컬 스튜디오 사용 안내" onClose={closeModal} />
          <div className="help-content">
            <p>
              1. WAV, MP3, FLAC 보컬 파일을 최대 3개 업로드하세요. 분석은
              백그라운드에서 진행됩니다.
            </p>
            <p>
              2. A/B/C 버튼으로 같은 타임라인의 트랙을 전환하세요. 음량 매칭은
              RMS 기준으로 더 큰 트랙을 감쇠합니다.
            </p>
            <p>
              3. 오프셋이 +1초이면 타임라인 0초에서 해당 파일의 1초를
              재생합니다. −1초이면 1초 뒤에 파일의 처음을 재생합니다.
            </p>
            <p>
              4. 분석 그래프 아래에서 비교 구간을 초 단위로 지정하고 반복
              재생하세요. 자동 음악적 정렬은 제공하지 않습니다.
            </p>
            <p>
              5. 트랙의 슬라이더 버튼으로 키·속도 변형본을 새 슬롯에 만들 수
              있습니다.
            </p>
            <p>
              6. 측정 리포트는 바로 사용할 수 있습니다. AI 평가는 설정의 제공자
              정보를 입력한 뒤 사용하세요.
            </p>
            <p className="muted">
              음원 분리는 별도 기능입니다. 반주가 포함된 파일은 보컬 분석
              정확도를 낮출 수 있습니다.
            </p>
          </div>
        </Modal>
      )}
      {deleteTarget && (
        <Modal title="삭제 확인" onClose={() => setDeleteTarget(null)}>
          <ModalHead
            title={`${deleteTarget.type === "project" ? "프로젝트" : "트랙"} 삭제`}
            onClose={() => setDeleteTarget(null)}
          />
          <div className="modal-form">
            <p>
              “{deleteTarget.name}”의 오디오와 분석 데이터를 삭제합니다. 복구할
              수 없습니다.
            </p>
            <div className="form-actions">
              <button
                className="secondary"
                onClick={() => setDeleteTarget(null)}
              >
                취소
              </button>
              <button className="danger-button" onClick={() => void remove()}>
                삭제
              </button>
            </div>
          </div>
        </Modal>
      )}
    </div>
  );
}

function Toggle({
  value,
  onChange,
  label,
}: {
  value: boolean;
  onChange: () => void;
  label: string;
}) {
  return (
    <button
      className={`pmd-mode-switch ${value ? "on" : ""}`}
      role="switch"
      aria-checked={value}
      aria-label={label}
      onClick={onChange}
    />
  );
}
function SettingRow({
  title,
  description,
  children,
}: {
  title: string;
  description: string;
  children?: React.ReactNode;
}) {
  return (
    <div className="setting-row">
      <div>
        <strong>{title}</strong>
        <p>{description}</p>
      </div>
      {children}
    </div>
  );
}
