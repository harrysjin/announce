import { useCallback, useEffect, useRef, useState } from "react";
import {
  ArrowDownToLine,
  ArrowLeft,
  ArrowRight,
  ArrowUpFromLine,
  BookOpen,
  Check,
  CheckCheck,
  ChevronDown,
  ChevronLeft,
  ChevronRight,
  CircleHelp,
  FileCheck2,
  FileText,
  Focus,
  Headphones,
  Highlighter,
  Info,
  Layers,
  Link2,
  LoaderCircle,
  Maximize2,
  Minus,
  MousePointer2,
  Plus,
  Redo2,
  RotateCcw,
  Search,
  Settings2,
  ShieldCheck,
  SlidersHorizontal,
  Sparkles,
  Trash2,
  Undo2,
  Upload,
  X,
} from "lucide-react";
import type {
  Candidate,
  Job,
  Mark,
  Occurrence,
  Settings,
  Snapshot,
} from "./types";

const DEFAULT: Settings = { color: "#CEE4F5", opacity: 0.6, size: 1 };
const SWATCHES = ["#CEE4F5", "#D7E8D1", "#F9E5B5", "#F2D4DC", "#DFD6F3"];
async function request<T>(url: string, init?: RequestInit): Promise<T> {
  const r = await fetch(url, init);
  if (!r.ok) {
    let detail = "요청을 처리하지 못했습니다.";
    try {
      const j = await r.json();
      detail = typeof j.detail === "string" ? j.detail : detail;
    } catch {}
    throw Object.assign(new Error(detail), { status: r.status });
  }
  try {
    return await r.json();
  } catch {
    throw new Error(
      "서버에서 올바른 응답을 받지 못했습니다. 잠시 후 다시 시도해 주세요.",
    );
  }
}
const copy = (j: Job): Snapshot =>
  structuredClone({
    settings: j.settings,
    marks: j.marks,
    occurrences: j.occurrences,
  });
const pronunciation = (c: Candidate) =>
  c.pronunciation || c.pronunciations?.join(", ") || "발음 정보 없음";
function Logo() {
  return (
    <a className="brand" href="/" aria-label="소리 홈">
      <span className="brand-symbol">
        <i />
        <i />
        <i />
        <i />
      </span>
      <span>
        소리<span className="brand-dot">.</span>
      </span>
      <span className="brand-caption">READING STUDIO</span>
    </a>
  );
}

export default function App() {
  const [job, setJob] = useState<Job | null>(null),
    [settings, setSettings] = useState<Settings>(DEFAULT),
    [tab, setTab] = useState<"settings" | "review">("settings");
  const [page, setPage] = useState(1),
    [zoom, setZoom] = useState(100),
    [edit, setEdit] = useState(false),
    [active, setActive] = useState<string | null>(null),
    [selectedMark, setSelectedMark] = useState<string | null>(null),
    [focusedOccurrenceId, setFocusedOccurrenceId] = useState<string | null>(
      null,
    ),
    [glyphFocus, setGlyphFocus] = useState(0);
  const [uploading, setUploading] = useState(false),
    [saving, setSaving] = useState(false),
    [drag, setDrag] = useState(false),
    [error, setError] = useState(""),
    [notice, setNotice] = useState(""),
    [help, setHelp] = useState(false),
    [downloads, setDownloads] = useState(false),
    [configured, setConfigured] = useState(false),
    [autoAnalyze, setAutoAnalyze] = useState(true);
  const [filter, setFilter] = useState("all"),
    [query, setQuery] = useState(""),
    [grouped, setGrouped] = useState(false),
    [mobileTab, setMobileTab] = useState<"pdf" | "tools">("pdf"),
    [past, setPast] = useState<Snapshot[]>([]),
    [future, setFuture] = useState<Snapshot[]>([]);
  const changing = useRef(false);
  const input = useRef<HTMLInputElement>(null),
    dropDepth = useRef(0);
  const jobRef = useRef(job);
  jobRef.current = job;
  const current = job?.pages.find((p) => p.number === page);
  useEffect(() => setGlyphFocus(0), [page, edit, job?.id]);
  const running = ["queued", "running"].includes(job?.analysis.state || "");
  useEffect(() => {
    request<{ dictionary_configured?: boolean }>("/api/health")
      .then((h) => setConfigured(!!h.dictionary_configured))
      .catch(() =>
        setError(
          "분석 서버에 연결할 수 없습니다. 서버 실행 상태를 확인해 주세요.",
        ),
      );
  }, []);
  // Persist only the per-tab job token, never the PDF or its contents.
  useEffect(() => {
    const id = sessionStorage.getItem("sori-job");
    if (!id) return;
    const controller = new AbortController();
    request<Job>(`/api/jobs/${id}`, { signal: controller.signal })
      .then((restored) => {
        if (jobRef.current && jobRef.current.id !== id) return;
        setJob(restored);
        setSettings(restored.settings);
        setNotice("이 탭에서 작업하던 대본을 이어서 불러왔습니다.");
      })
      .catch((e) => {
        if (e.name === "AbortError") return;
        if (e.status === 404) {
          sessionStorage.removeItem("sori-job");
          setNotice("이전 작업이 만료되었습니다. PDF를 다시 올려주세요.");
        } else
          setError(
            "이전 작업에 연결하지 못했습니다. 잠시 후 새로고침해 주세요.",
          );
      });
    return () => controller.abort();
  }, []);
  useEffect(() => {
    if (job) sessionStorage.setItem("sori-job", job.id);
  }, [job?.id]);
  useEffect(() => {
    if (!running || !job) return;
    const id = job.id,
      controller = new AbortController();
    let timer: ReturnType<typeof setTimeout>;
    const poll = async () => {
      try {
        const progress = await request<Pick<Job, "id" | "analysis">>(
          `/api/jobs/${id}/progress`,
          { signal: controller.signal },
        );
        if (controller.signal.aborted || jobRef.current?.id !== id) return;
        if (["running", "queued"].includes(progress.analysis.state)) {
          setJob((current) =>
            current?.id === id
              ? { ...current, analysis: progress.analysis }
              : current,
          );
          timer = setTimeout(poll, 1000);
        } else {
          const next = await request<Job>(`/api/jobs/${id}`, {
            signal: controller.signal,
          });
          if (!controller.signal.aborted && jobRef.current?.id === id) {
            setJob(next);
            setSettings(next.settings);
          }
        }
      } catch (e) {
        const failure = e as Error & { status?: number };
        if (failure.name === "AbortError") return;
        if (failure.status === 404) {
          sessionStorage.removeItem("sori-job");
          setJob(null);
          setError("작업이 만료되었습니다. PDF를 다시 올려주세요.");
        } else {
          setError("진행 상태를 확인하지 못했습니다. 연결을 다시 시도합니다.");
          timer = setTimeout(poll, 3000);
        }
      }
    };
    timer = setTimeout(poll, 500);
    return () => {
      clearTimeout(timer);
      controller.abort();
    };
  }, [running, job?.id]);
  useEffect(() => {
    if (!help) return;
    const previous = document.activeElement as HTMLElement | null;
    const modal = document.querySelector<HTMLElement>(".guide-modal");
    modal?.querySelector<HTMLElement>("button")?.focus();
    const handle = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        event.preventDefault();
        setHelp(false);
      }
      if (event.key !== "Tab" || !modal) return;
      const items = Array.from(
        modal.querySelectorAll<HTMLElement>(
          'button:not(:disabled), a[href], input, textarea, [tabindex="0"]',
        ),
      );
      const first = items[0],
        last = items[items.length - 1];
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last?.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first?.focus();
      }
    };
    document.addEventListener("keydown", handle);
    return () => {
      document.removeEventListener("keydown", handle);
      previous?.focus();
    };
  }, [help]);
  useEffect(() => {
    if (!notice) return;
    const t = setTimeout(() => setNotice(""), 4000);
    return () => clearTimeout(t);
  }, [notice]);
  const patch = useCallback(async (snap: Snapshot, history = true) => {
    const j = jobRef.current;
    if (!j || changing.current) return false;
    changing.current = true;
    const previous = copy(j);
    setSaving(true);
    setError("");
    try {
      const next = await request<Job>(`/api/jobs/${j.id}/state`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ ...snap, restore: !history }),
      });
      if (jobRef.current?.id !== j.id) return false;
      setJob(next);
      setSettings(next.settings);
      if (history) {
        setPast((p) => [...p, previous].slice(-40));
        setFuture([]);
      }
      return true;
    } catch (e) {
      if (jobRef.current?.id === j.id) setSettings(j.settings);
      setError((e as Error).message);
      return false;
    } finally {
      changing.current = false;
      setSaving(false);
    }
  }, []);
  const changeSettings = (next: Settings) => {
    setSettings(next);
    if (job) void patch({ ...copy(job), settings: next });
  };
  const undo = useCallback(async () => {
    if (!job || !past.length || saving) return;
    const prev = past[past.length - 1];
    if (await patch(prev, false)) {
      setFuture((f) => [copy(job), ...f]);
      setPast((p) => p.slice(0, -1));
    }
  }, [job, past, saving, patch]);
  const redo = useCallback(async () => {
    if (!job || !future.length || saving) return;
    const next = future[0];
    if (await patch(next, false)) {
      setPast((p) => [...p, copy(job)]);
      setFuture((f) => f.slice(1));
    }
  }, [job, future, saving, patch]);
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if (
        (e.ctrlKey || e.metaKey) &&
        e.key === "z" &&
        !(
          e.target instanceof HTMLInputElement ||
          e.target instanceof HTMLTextAreaElement
        )
      ) {
        e.preventDefault();
        if (e.shiftKey) void redo();
        else void undo();
      }
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [undo, redo]);
  async function upload(file: File) {
    if (uploading || changing.current) return;
    if (running) {
      setError("분석을 중단한 후 다른 PDF를 열어주세요.");
      return;
    }
    if (!file.name.toLowerCase().endsWith(".pdf")) {
      setError("PDF 파일을 선택해 주세요.");
      return;
    }
    if (file.size > 20 * 1024 * 1024) {
      setError("20MB 이하의 PDF 파일을 선택해 주세요.");
      return;
    }
    setUploading(true);
    setError("");
    try {
      const data = new FormData();
      data.append("file", file);
      let j = await request<Job>("/api/jobs", { method: "POST", body: data });
      if (JSON.stringify(j.settings) !== JSON.stringify(settings))
        j = await request<Job>(`/api/jobs/${j.id}/state`, {
          method: "PATCH",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ settings }),
        });
      const previous = jobRef.current;
      setJob(j);
      jobRef.current = j;
      setSettings(j.settings);
      setPage(1);
      setPast([]);
      setFuture([]);
      setActive(null);
      setFocusedOccurrenceId(null);
      setSelectedMark(null);
      setTab("settings");
      if (previous)
        fetch(`/api/jobs/${previous.id}`, { method: "DELETE" }).catch(() => {});
      if (autoAnalyze && j.dictionary_configured === true) {
        if (await analyze(j))
          setNotice("대본을 불러왔습니다. 장음 분석을 시작합니다.");
      } else {
        setNotice(
          j.dictionary_configured === true
            ? "대본을 불러왔습니다. 장음 분석을 시작해 보세요."
            : "대본을 불러왔습니다. 직접 표시와 다운로드를 사용할 수 있습니다.",
        );
      }
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setUploading(false);
      if (input.current) input.current.value = "";
    }
  }
  async function sample() {
    setError("");
    try {
      const r = await fetch("/api/sample");
      if (!r.ok) throw new Error("예시 대본을 불러오지 못했습니다.");
      await upload(
        new File([await r.blob()], "소리_예시대본.pdf", {
          type: "application/pdf",
        }),
      );
    } catch (e) {
      setError((e as Error).message);
    }
  }
  async function analyze(target: Job | null = job) {
    if (!target) return;
    setError("");
    try {
      const j = await request<Job>(`/api/jobs/${target.id}/analyze`, {
        method: "POST",
      });
      if (jobRef.current?.id !== target.id) return;
      setJob(j);
      setTab("review");
      setMobileTab("tools");
      setPast([]);
      setFuture([]);
      return true;
    } catch (e) {
      setError((e as Error).message);
      return false;
    }
  }
  async function cancel() {
    if (!job) return;
    try {
      setJob(
        await request<Job>(`/api/jobs/${job.id}/cancel`, { method: "POST" }),
      );
    } catch (e) {
      setError((e as Error).message);
    }
  }
  async function removeJob() {
    if (!job) return;
    try {
      await request(`/api/jobs/${job.id}`, { method: "DELETE" });
      sessionStorage.removeItem("sori-job");
      setJob(null);
      setPage(1);
      setPast([]);
      setFuture([]);
      setNotice("파일과 작업을 삭제했습니다.");
    } catch (e) {
      setError((e as Error).message);
    }
  }
  async function choose(o: Occurrence, c: Candidate) {
    if (!job || changing.current) return;
    changing.current = true;
    const previous = copy(job);
    setSaving(true);
    try {
      setJob(
        await request<Job>(
          `/api/jobs/${job.id}/occurrences/${o.id}/candidate`,
          {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ candidate_id: c.id }),
          },
        ),
      );
      setPast((p) => [...p, previous].slice(-40));
      setFuture([]);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      changing.current = false;
      setSaving(false);
    }
  }
  const toggleGlyph = (glyphId: string) => {
    if (!job || saving || running) return;
    const found = job.marks.find((m) => m.glyph_id === glyphId);
    if (found) {
      setSelectedMark(found.id);
      setTab("settings");
      return;
    }
    const mark: Mark = {
      id: crypto.randomUUID(),
      glyph_id: glyphId,
      page,
      source: "manual",
      dx: 0,
      dy: 0,
      scale: 1,
    };
    void patch({ ...copy(job), marks: [...job.marks, mark] });
    setSelectedMark(mark.id);
    setTab("settings");
  };
  const modifyMark = (delta: Partial<Mark>) => {
    if (!job || !selectedMark) return;
    void patch({
      ...copy(job),
      marks: job.marks.map((m) =>
        m.id === selectedMark ? { ...m, ...delta } : m,
      ),
    });
  };
  const removeMark = () => {
    if (!job || !selectedMark) return;
    void patch({
      ...copy(job),
      marks: job.marks.filter((m) => m.id !== selectedMark),
    });
    setSelectedMark(null);
  };
  async function download(format: "pdf" | "md" | "csv") {
    if (!job || saving) return;
    setDownloads(false);
    setError("");
    try {
      const r = await fetch(`/api/jobs/${job.id}/export/${format}`);
      if (!r.ok) {
        const d = await r.json();
        throw new Error(d.detail || "다운로드에 실패했습니다.");
      }
      const url = URL.createObjectURL(await r.blob());
      const a = document.createElement("a");
      a.href = url;
      a.download =
        job.filename.replace(/\.pdf$/i, "") +
        (format === "pdf"
          ? "_장음표시.pdf"
          : format === "md"
            ? "_장음검증.md"
            : "_장음목록.csv");
      a.click();
      setTimeout(() => URL.revokeObjectURL(url), 2000);
      setNotice("현재 표시를 반영해 다운로드했습니다.");
    } catch (e) {
      setError((e as Error).message);
    }
  }
  const occurrences = job?.occurrences || [],
    unresolved = occurrences.filter((o) => o.status === "unresolved").length,
    meaningsPending = occurrences.filter(
      (o) => o.meaning_status === "unresolved",
    ).length,
    confirmed = occurrences.filter(
      (o) => o.status === "long" && o.source !== "manual",
    ).length;
  let filtered = occurrences.filter(
    (o) =>
      (filter === "all" ||
        o.status === filter ||
        (filter === "meaning" && o.meaning_status === "unresolved")) &&
      `${o.text} ${o.lemma} ${o.reason}`.includes(query),
  );
  const groupKey = (o: Occurrence) =>
    o.selected_candidate_id
      ? JSON.stringify([
          o.lemma,
          o.selected_candidate_id,
          o.status,
          o.text,
          o.meaning_status,
          o.evidence_type,
          o.pronunciations,
        ])
      : o.id;
  const groupMembers = (o: Occurrence) =>
    occurrences.filter((other) => groupKey(other) === groupKey(o));
  if (grouped) {
    const seen = new Set<string>();
    filtered = filtered.filter((o) => {
      const k = groupKey(o);
      if (seen.has(k)) return false;
      seen.add(k);
      return true;
    });
  }
  useEffect(() => {
    if (!active) return;
    const timer = setTimeout(
      () =>
        document.querySelector(".active-occurrence-box")?.scrollIntoView({
          block: "center",
          inline: "center",
          behavior: "smooth",
        }),
      60,
    );
    return () => clearTimeout(timer);
  }, [active, focusedOccurrenceId, page, mobileTab]);
  const selected = job?.marks.find((m) => m.id === selectedMark);
  const activeOccurrence = occurrences.find(
    (o) => o.id === (focusedOccurrenceId || active),
  );
  const pageMarks = job?.marks.filter((m) => m.page === page) || [];
  const qualityWarnings =
    job?.pages.filter((p) => !["text", "ok", "good"].includes(p.quality)) || [];
  return (
    <div className="app">
      <header className="site-header">
        <div className="header-inner">
          <Logo />
          <nav>
            <span className="nav-current">작업 공간</span>
            <button onClick={() => setHelp(true)}>
              이용 가이드 <CircleHelp size={14} />
            </button>
          </nav>
          <span className="header-tag">
            <span />
            당신의 목소리에 집중하는 시간
          </span>
        </div>
      </header>
      <main>
        <section className="intro">
          <div>
            <div className="eyebrow">A LITTLE MARK. A BETTER READ.</div>
            <h1>
              읽는 순간에 집중하세요<span>.</span>
            </h1>
            <p>장음은 가볍게 표시하고, 낭독은 더 자연스럽게.</p>
          </div>
          <div className="intro-note">
            <span className="note-icon">
              <Headphones size={22} />
            </span>
            <div>
              <strong>좋은 낭독의 작은 차이</strong>
              <span>대본 위에 더하는 정확한 호흡</span>
            </div>
          </div>
        </section>
        {error && (
          <div className="error-banner" role="alert">
            <Info size={17} />
            <span>{error}</span>
            <button aria-label="오류 닫기" onClick={() => setError("")}>
              <X size={16} />
            </button>
          </div>
        )}
        <section
          className="workspace"
          onDragEnter={(e) => {
            if (!Array.from(e.dataTransfer.types).includes("Files")) return;
            e.preventDefault();
            dropDepth.current++;
            setDrag(true);
          }}
          onDragLeave={(e) => {
            e.preventDefault();
            if (--dropDepth.current <= 0) setDrag(false);
          }}
          onDragOver={(e) => e.preventDefault()}
          onDrop={(e) => {
            e.preventDefault();
            dropDepth.current = 0;
            setDrag(false);
            if (e.dataTransfer.files[0]) void upload(e.dataTransfer.files[0]);
          }}
        >
          {drag && (
            <div className="drag-overlay">
              <Upload size={40} />
              <strong>여기에 PDF를 놓아주세요</strong>
            </div>
          )}
          <div className="workspace-toolbar">
            <div className="document-name">
              <span className="file-icon">
                <FileText size={19} />
              </span>
              <div>
                <strong title={job?.filename}>
                  {job?.filename || "새로운 대본"}
                </strong>
                <span>
                  {job
                    ? `${job.pages.length}페이지 · ${new Set(job.occurrences.map((o) => o.lemma)).size}개 어휘 · ${job.occurrences.length}회 출현`
                    : "PDF를 올리고 나만의 낭독을 준비하세요"}
                </span>
              </div>
              {job && (
                <button
                  className="icon-button replace"
                  onClick={() => input.current?.click()}
                  aria-label="다른 PDF 열기"
                  disabled={uploading || running}
                >
                  <Upload size={16} />
                </button>
              )}
            </div>
            <div className="toolbar-actions">
              <div className="history-actions">
                <button
                  aria-label="실행 취소"
                  title="실행 취소 (⌘Z)"
                  disabled={!past.length || saving || running}
                  onClick={() => void undo()}
                >
                  <Undo2 size={17} />
                </button>
                <button
                  aria-label="다시 실행"
                  title="다시 실행 (⇧⌘Z)"
                  disabled={!future.length || saving || running}
                  onClick={() => void redo()}
                >
                  <Redo2 size={17} />
                </button>
              </div>
              <button
                className="analyze-button"
                disabled={!job || uploading || saving}
                onClick={() => (running ? void cancel() : void analyze())}
              >
                {running ? (
                  <LoaderCircle size={15} className="spin" />
                ) : (
                  <Sparkles size={15} />
                )}
                <span>{running ? "분석 중단" : "장음 분석"}</span>
              </button>
              <div className="download-wrap">
                <button
                  className="dark-button"
                  disabled={!job || running || saving}
                  onClick={() => setDownloads(!downloads)}
                >
                  <ArrowDownToLine size={15} />
                  <span>다운로드</span>
                  <ChevronDown size={13} />
                </button>
                {downloads && (
                  <>
                    <div
                      className="menu-scrim"
                      onClick={() => setDownloads(false)}
                    />
                    <div className="download-menu">
                      <div className="export-status">
                        <strong>
                          {job?.analysis.state === "idle"
                            ? "자동 분석 전"
                            : job?.analysis.state === "waiting_key"
                              ? "사전 연결 대기"
                              : "현재 검토 결과"}
                        </strong>
                        <span>
                          표시 {job?.marks.length || 0}개 · 장단 미확정{" "}
                          {unresolved}개
                        </span>
                        {meaningsPending > 0 && (
                          <span>뜻 선택 미확정 {meaningsPending}개 포함</span>
                        )}
                      </div>
                      <button onClick={() => void download("pdf")}>
                        <FileCheck2 size={17} />
                        <span>
                          장음 표시 PDF<small>현재 설정과 수동 수정 반영</small>
                        </span>
                      </button>
                      <button onClick={() => void download("md")}>
                        <BookOpen size={17} />
                        <span>
                          검증 기록<small>근거와 미확정 항목 · Markdown</small>
                        </span>
                      </button>
                      <button onClick={() => void download("csv")}>
                        <Layers size={17} />
                        <span>
                          장음 목록<small>전체 출현 위치 · CSV</small>
                        </span>
                      </button>
                    </div>
                  </>
                )}
              </div>
            </div>
          </div>
          <div className="mobile-tabs">
            <button
              className={mobileTab === "pdf" ? "active" : ""}
              onClick={() => setMobileTab("pdf")}
            >
              <FileText size={15} />
              대본
            </button>
            <button
              className={mobileTab === "tools" ? "active" : ""}
              onClick={() => setMobileTab("tools")}
            >
              <Settings2 size={15} />
              설정 및 검토
            </button>
          </div>
          <div className="workspace-body">
            <aside
              className={`pages-sidebar ${mobileTab !== "pdf" ? "mobile-hidden" : ""}`}
            >
              <div className="sidebar-label">
                {job ? "페이지" : "시작하기"}
                <span>
                  {job ? String(job.pages.length).padStart(2, "0") : "01—03"}
                </span>
              </div>
              {job ? (
                <div className="thumbnails">
                  {job.pages.map((p) => (
                    <button
                      key={p.number}
                      className={`thumbnail ${page === p.number ? "selected" : ""}`}
                      onClick={() => {
                        setPage(p.number);
                        setSelectedMark(null);
                      }}
                    >
                      <span className="thumbnail-paper">
                        <img
                          src={`/api/jobs/${job.id}/pages/${p.number}/preview.png?marked=false`}
                          alt={`${p.number}페이지 미리보기`}
                        />
                        {p.quality === "scan" && (
                          <span className="scan-tag">스캔</span>
                        )}
                      </span>
                      <span>{p.number}</span>
                    </button>
                  ))}
                </div>
              ) : (
                <div className="steps">
                  <div className="step current">
                    <span>1</span>
                    <div>
                      <strong>대본 올리기</strong>
                      <small>PDF 파일 선택</small>
                    </div>
                  </div>
                  <div className="step">
                    <span>2</span>
                    <div>
                      <strong>장음 확인하기</strong>
                      <small>분석과 검토</small>
                    </div>
                  </div>
                  <div className="step">
                    <span>3</span>
                    <div>
                      <strong>낭독 준비 완료</strong>
                      <small>표시된 PDF 저장</small>
                    </div>
                  </div>
                  <div className="sidebar-line-art">
                    <div className="tiny-sheet">
                      <i />
                      <i />
                      <i />
                      <i />
                      <i />
                    </div>
                    <span className="tiny-circle" />
                  </div>
                </div>
              )}
              <div className="sidebar-bottom">
                <ShieldCheck size={15} />
                <span>
                  나만 보는 대본
                  <br />
                  <small>임시 작업 · 직접 삭제 가능</small>
                </span>
              </div>
            </aside>
            <div
              className={`document-area ${mobileTab !== "pdf" ? "mobile-hidden" : ""}`}
            >
              <div className="viewer-toolbar">
                <span>
                  <span className={`status-dot ${job ? "ready" : ""}`} />
                  {job ? "대본 미리보기" : "대본을 기다리고 있어요"}
                </span>
                <div>
                  {job && (
                    <button
                      className={edit ? "edit-toggle active" : "edit-toggle"}
                      disabled={running || saving}
                      onClick={() => setEdit(!edit)}
                      title="음절을 클릭해 표시 추가"
                    >
                      <Highlighter size={14} />
                      <span>직접 표시</span>
                    </button>
                  )}
                  <button
                    className="icon-button"
                    aria-label="축소"
                    disabled={!job || zoom <= 50}
                    onClick={() => setZoom((z) => z - 10)}
                  >
                    <Minus size={14} />
                  </button>
                  <span className="zoom-value">{zoom}%</span>
                  <button
                    className="icon-button"
                    aria-label="확대"
                    disabled={!job || zoom >= 200}
                    onClick={() => setZoom((z) => z + 10)}
                  >
                    <Plus size={14} />
                  </button>
                  <span className="viewer-divider" />
                  <button
                    className="icon-button"
                    aria-label="화면에 맞추기"
                    onClick={() => setZoom(100)}
                    disabled={!job}
                  >
                    <Maximize2 size={14} />
                  </button>
                </div>
              </div>
              {!job ? (
                <div className="empty-stage">
                  <div className="upload-zone">
                    <div className="upload-illustration">
                      <div className="paper-back" />
                      <div className="paper-front">
                        <span>PDF</span>
                        <i />
                        <i />
                        <i />
                        <i />
                        <span className="paper-highlight" />
                      </div>
                      <div className="upload-badge">
                        <ArrowUpFromLine size={21} strokeWidth={1.8} />
                      </div>
                    </div>
                    <span className="micro-label">YOUR NEXT GREAT READ</span>
                    <h2>대본을 여기에 놓아주세요</h2>
                    <p>
                      표시 하나로 달라지는 낭독.
                      <br />첫 번째 PDF를 올려 시작해 보세요.
                    </p>
                    <button
                      className="blue-button upload-button"
                      disabled={uploading}
                      onClick={() => input.current?.click()}
                    >
                      {uploading ? (
                        <LoaderCircle className="spin" size={17} />
                      ) : (
                        <Plus size={17} />
                      )}{" "}
                      {uploading ? "대본 불러오는 중" : "PDF 파일 선택"}
                    </button>
                    <span className="upload-hint">
                      텍스트형 PDF · 최대 20MB · 최대 100페이지
                    </span>
                    <div className="sample-divider">
                      <span />
                      또는
                      <span />
                    </div>
                    <button
                      className="sample-button"
                      disabled={uploading}
                      onClick={() => void sample()}
                    >
                      예시 대본으로 둘러보기 <ArrowRight size={14} />
                    </button>
                  </div>
                  <div className="empty-stage-footer">
                    <span>
                      <Check size={13} />
                      원본 레이아웃 유지
                    </span>
                    <span>
                      <Check size={13} />
                      검색 가능한 PDF
                    </span>
                  </div>
                </div>
              ) : (
                <div className="pdf-stage">
                  {Array.isArray(job.warnings) &&
                    job.warnings.filter(
                      (w: unknown) =>
                        typeof w === "string" && w.startsWith("표시 "),
                    ).length > 0 && (
                      <div className="quality-note">
                        <Info size={14} />
                        <span>
                          원형 표시가 겹치거나 페이지 경계에 닿습니다.
                          위치·크기를 확인해 주세요.
                        </span>
                      </div>
                    )}
                  {qualityWarnings.length > 0 && (
                    <div className="quality-note">
                      <Info size={14} />
                      <span>
                        {qualityWarnings.length}개 페이지의 텍스트 추출을 확인해
                        주세요. 스캔 페이지는 OCR을 지원하지 않습니다.
                      </span>
                    </div>
                  )}
                  {edit && (
                    <div className="edit-note">
                      <MousePointer2 size={14} /> 표시할 음절을 클릭하세요. 원을
                      클릭하면 위치를 조절할 수 있어요. 키보드는 Tab으로
                      진입하고 좌우 방향키·Enter를 사용하세요.
                    </div>
                  )}
                  {current && (
                    <div
                      className="pdf-paper"
                      style={{
                        width: `${zoom}%`,
                        aspectRatio: `${current.width}/${current.height}`,
                      }}
                    >
                      <img
                        src={`/api/jobs/${job.id}/pages/${page}/preview.png?marked=false`}
                        alt={`${page}페이지 대본 원문`}
                      />
                      <svg
                        className={`mark-layer ${edit ? "editing" : ""}`}
                        viewBox={`0 0 ${current.width} ${current.height}`}
                        aria-label="장음 표시"
                      >
                        {pageMarks.map((m) => {
                          const g = current.glyphs.find(
                            (g) => g.id === m.glyph_id,
                          );
                          if (!g) return null;
                          const cx = (g.bbox[0] + g.bbox[2]) / 2 + m.dx,
                            cy = (g.bbox[1] + g.bbox[3]) / 2 + m.dy,
                            r = ((g.size + 3) * settings.size * m.scale) / 2;
                          return (
                            <g
                              key={m.id}
                              className="mark"
                              role="button"
                              tabIndex={0}
                              aria-label={`${g.text} 장음 표시 선택`}
                              onKeyDown={(event) => {
                                if (
                                  event.key === "Enter" ||
                                  event.key === " "
                                ) {
                                  event.preventDefault();
                                  setSelectedMark(m.id);
                                  setTab("settings");
                                  setMobileTab("tools");
                                }
                              }}
                              onClick={() => {
                                setSelectedMark(m.id);
                                setTab("settings");
                                setMobileTab("tools");
                              }}
                            >
                              <circle
                                cx={cx}
                                cy={cy}
                                r={r}
                                fill={settings.color}
                                opacity={settings.opacity}
                                style={{ mixBlendMode: "multiply" }}
                              />
                              {m.id === selectedMark && (
                                <circle
                                  cx={cx}
                                  cy={cy}
                                  r={r + 2}
                                  fill="none"
                                  stroke="#0071e3"
                                  strokeWidth="0.8"
                                  strokeDasharray="2 2"
                                />
                              )}
                              <title>
                                {g.text} ·{" "}
                                {m.source === "manual"
                                  ? "사용자 표시"
                                  : "사전 근거 표시"}
                              </title>
                            </g>
                          );
                        })}
                        {activeOccurrence?.page === page &&
                          activeOccurrence.glyph_ids.map((id) => {
                            const g = current.glyphs.find((g) => g.id === id);
                            return (
                              g && (
                                <rect
                                  className="active-occurrence-box"
                                  key={id}
                                  x={g.bbox[0]}
                                  y={g.bbox[1]}
                                  width={g.bbox[2] - g.bbox[0]}
                                  height={g.bbox[3] - g.bbox[1]}
                                  fill="#0071e3"
                                  opacity="0.12"
                                  pointerEvents="none"
                                />
                              )
                            );
                          })}
                        {edit &&
                          current.glyphs
                            .filter((g) => g.text.trim())
                            .map((g, index) => (
                              <rect
                                className="glyph-target"
                                role="button"
                                tabIndex={index === glyphFocus ? 0 : -1}
                                data-glyph-id={g.id}
                                aria-label={`${page}쪽 ${index + 1}번째 글자 ${g.text}, 장음 표시 추가 또는 선택`}
                                onFocus={() => setGlyphFocus(index)}
                                onKeyDown={(event) => {
                                  if (
                                    event.key === "Enter" ||
                                    event.key === " "
                                  ) {
                                    event.preventDefault();
                                    toggleGlyph(g.id);
                                  }
                                  if (
                                    ["ArrowRight", "ArrowLeft"].includes(
                                      event.key,
                                    )
                                  ) {
                                    event.preventDefault();
                                    const letters = current.glyphs.filter(
                                      (letter) => letter.text.trim(),
                                    );
                                    const next = Math.max(
                                      0,
                                      Math.min(
                                        letters.length - 1,
                                        index +
                                          (event.key === "ArrowRight" ? 1 : -1),
                                      ),
                                    );
                                    setGlyphFocus(next);
                                    document
                                      .querySelector<SVGElement>(
                                        `[data-glyph-id="${letters[next].id}"]`,
                                      )
                                      ?.focus();
                                  }
                                }}
                                key={g.id}
                                x={g.bbox[0]}
                                y={g.bbox[1]}
                                width={g.bbox[2] - g.bbox[0]}
                                height={g.bbox[3] - g.bbox[1]}
                                onClick={() => toggleGlyph(g.id)}
                              >
                                <title>{g.text}에 장음 표시</title>
                              </rect>
                            ))}
                      </svg>
                    </div>
                  )}
                </div>
              )}
              <div className="viewer-footer">
                <span>
                  {job ? (
                    <>
                      <FileText size={13} />
                      {job.marks.length}개 음절 표시{saving ? " · 저장 중" : ""}
                    </>
                  ) : (
                    <>
                      <Focus size={14} />
                      정확한 음절 위에, 가볍게.
                    </>
                  )}
                </span>
                <div>
                  <button
                    aria-label="이전 페이지"
                    disabled={!job || page <= 1}
                    onClick={() => setPage((p) => p - 1)}
                  >
                    <ChevronLeft size={15} />
                  </button>
                  <span>
                    {job ? page : "—"} <i>/</i> {job?.pages.length || "—"}
                  </span>
                  <button
                    aria-label="다음 페이지"
                    disabled={!job || page >= job.pages.length}
                    onClick={() => setPage((p) => p + 1)}
                  >
                    <ChevronRight size={15} />
                  </button>
                </div>
              </div>
            </div>
            <aside
              className={`inspector ${mobileTab !== "tools" ? "mobile-hidden" : ""}`}
            >
              <div className="inspector-tabs">
                <button
                  className={tab === "settings" ? "active" : ""}
                  onClick={() => setTab("settings")}
                >
                  <SlidersHorizontal size={15} />
                  표시 설정
                </button>
                <button
                  className={tab === "review" ? "active" : ""}
                  onClick={() => setTab("review")}
                >
                  <CheckCheck size={15} />
                  검토 목록
                  {unresolved > 0 && (
                    <span className="tab-count">{unresolved}</span>
                  )}
                </button>
              </div>
              {tab === "settings" ? (
                <div className="settings-panel">
                  <div className="panel-heading">
                    <h3>나에게 편안한 표시</h3>
                    <p>대본은 그대로, 필요한 부분만 또렷하게.</p>
                  </div>
                  <section className="control-section">
                    <label className="control-label">
                      형광펜 색상{" "}
                      <button
                        aria-label="기본 색상으로 복원"
                        onClick={() =>
                          changeSettings({ ...settings, color: DEFAULT.color })
                        }
                        disabled={saving || running}
                      >
                        <RotateCcw size={13} />
                      </button>
                    </label>
                    <div className="swatches">
                      {SWATCHES.map((c) => (
                        <button
                          key={c}
                          aria-label={`${c} 색상`}
                          title={c}
                          style={{ backgroundColor: c }}
                          className={
                            settings.color.toUpperCase() === c ? "chosen" : ""
                          }
                          onClick={() =>
                            changeSettings({ ...settings, color: c })
                          }
                          disabled={saving || running}
                        >
                          {settings.color.toUpperCase() === c && (
                            <Check size={16} />
                          )}
                        </button>
                      ))}
                      <label className="custom-swatch" title="직접 색상 선택">
                        <Plus size={17} />
                        <input
                          aria-label="색상 선택기"
                          type="color"
                          value={settings.color}
                          onChange={(e) =>
                            setSettings({ ...settings, color: e.target.value })
                          }
                          onBlur={(e) =>
                            changeSettings({
                              ...settings,
                              color: e.target.value,
                            })
                          }
                          disabled={saving || running}
                        />
                      </label>
                    </div>
                    <div className="hex-input">
                      <span style={{ backgroundColor: settings.color }} />
                      <input
                        aria-label="HEX 색상"
                        value={settings.color.toUpperCase()}
                        maxLength={7}
                        onChange={(e) =>
                          setSettings({ ...settings, color: e.target.value })
                        }
                        onBlur={(e) => {
                          if (/^#[0-9a-f]{6}$/i.test(e.target.value))
                            changeSettings({
                              ...settings,
                              color: e.target.value,
                            });
                          else {
                            setSettings((s) => ({
                              ...s,
                              color: job?.settings.color || DEFAULT.color,
                            }));
                            setError(
                              "색상은 #CEE4F5처럼 6자리 HEX로 입력해 주세요.",
                            );
                          }
                        }}
                        disabled={saving || running}
                      />
                      <small>HEX</small>
                    </div>
                  </section>
                  <section className="control-section range-controls">
                    <label className="control-label" htmlFor="size">
                      표시 크기{" "}
                      <span>
                        {Math.round(settings.size * 100)}
                        <small>%</small>
                      </span>
                    </label>
                    <input
                      id="size"
                      type="range"
                      min="0.6"
                      max="1.6"
                      step="0.05"
                      value={settings.size}
                      onChange={(e) =>
                        setSettings({ ...settings, size: +e.target.value })
                      }
                      onPointerUp={(e) =>
                        changeSettings({
                          ...settings,
                          size: +e.currentTarget.value,
                        })
                      }
                      onKeyUp={(e) =>
                        changeSettings({
                          ...settings,
                          size: +e.currentTarget.value,
                        })
                      }
                      disabled={saving || running}
                    />
                    <div className="range-labels">
                      <span>작게</span>
                      <span>크게</span>
                    </div>
                    <label
                      className="control-label opacity-label"
                      htmlFor="opacity"
                    >
                      불투명도{" "}
                      <span>
                        {Math.round(settings.opacity * 100)}
                        <small>%</small>
                      </span>
                    </label>
                    <input
                      id="opacity"
                      type="range"
                      min="0.1"
                      max="1"
                      step="0.05"
                      value={settings.opacity}
                      onChange={(e) =>
                        setSettings({ ...settings, opacity: +e.target.value })
                      }
                      onPointerUp={(e) =>
                        changeSettings({
                          ...settings,
                          opacity: +e.currentTarget.value,
                        })
                      }
                      onKeyUp={(e) =>
                        changeSettings({
                          ...settings,
                          opacity: +e.currentTarget.value,
                        })
                      }
                      disabled={saving || running}
                    />
                    <div className="range-labels">
                      <span>은은하게</span>
                      <span>선명하게</span>
                    </div>
                  </section>
                  <div className="live-preview">
                    <div className="preview-label">
                      <span />
                      표시 미리보기
                    </div>
                    <p>
                      <span className="demo-syllable">
                        말
                        <i
                          style={{
                            background: settings.color,
                            opacity: settings.opacity,
                            transform: `translate(-50%, -50%) scale(${settings.size})`,
                          }}
                        />
                      </span>{" "}
                      한마디에도,
                      <br />
                      깊이가 생기도록.
                    </p>
                    <span className="preview-caption">
                      모양 확인용 예시입니다
                    </span>
                  </div>
                  {selected && (
                    <section className="selected-mark">
                      <div className="control-label">
                        선택한 표시{" "}
                        <button
                          aria-label="선택 해제"
                          onClick={() => setSelectedMark(null)}
                        >
                          <X size={14} />
                        </button>
                      </div>
                      <span className="manual-label">
                        {selected.source === "manual"
                          ? "사용자 수정"
                          : "사전 표시"}{" "}
                        · {selected.page}쪽
                      </span>
                      <div className="position-controls">
                        <button
                          aria-label="표시 왼쪽 이동"
                          disabled={saving}
                          onClick={() => modifyMark({ dx: selected.dx - 1 })}
                        >
                          <ArrowLeft size={14} />
                        </button>
                        <button
                          aria-label="표시 위 이동"
                          disabled={saving}
                          onClick={() => modifyMark({ dy: selected.dy - 1 })}
                        >
                          <ArrowUpFromLine size={14} />
                        </button>
                        <button
                          aria-label="표시 아래 이동"
                          disabled={saving}
                          onClick={() => modifyMark({ dy: selected.dy + 1 })}
                        >
                          <ArrowDownToLine size={14} />
                        </button>
                        <button
                          aria-label="표시 오른쪽 이동"
                          disabled={saving}
                          onClick={() => modifyMark({ dx: selected.dx + 1 })}
                        >
                          <ArrowRight size={14} />
                        </button>
                      </div>
                      <label className="control-label" htmlFor="mark-size">
                        이 표시 크기{" "}
                        <span>{Math.round(selected.scale * 100)}%</span>
                      </label>
                      <input
                        id="mark-size"
                        type="range"
                        min="0.6"
                        max="1.6"
                        step="0.05"
                        value={selected.scale}
                        onChange={(e) => modifyMark({ scale: +e.target.value })}
                        disabled={saving}
                      />
                      <button
                        className="remove-mark"
                        onClick={removeMark}
                        disabled={saving}
                      >
                        <Trash2 size={13} />
                        표시 삭제
                      </button>
                    </section>
                  )}
                  {!job && (
                    <label className="auto-analysis-option">
                      <input
                        type="checkbox"
                        checked={autoAnalyze}
                        onChange={(e) => setAutoAnalyze(e.target.checked)}
                      />
                      <span>
                        업로드 후 자동으로 장음 분석
                        <small>사전이 연결되어 있을 때 바로 시작합니다.</small>
                      </span>
                    </label>
                  )}
                  <button
                    className="reset-settings"
                    onClick={() => changeSettings(DEFAULT)}
                    disabled={saving || running}
                  >
                    <RotateCcw size={13} />
                    기본 설정으로 되돌리기
                  </button>
                  <div className="setting-tip">
                    <Info size={15} />
                    <p>
                      설정을 바꿔도 다시 분석할 필요 없어요.
                      <br />
                      미리보기와 PDF에 함께 반영됩니다.
                    </p>
                  </div>
                </div>
              ) : (
                <div className="review-panel">
                  <div className="review-summary">
                    <div>
                      <strong>{confirmed}</strong>
                      <span>장음 위치 확인</span>
                    </div>
                    <div>
                      <strong>{unresolved}</strong>
                      <span>장단 미확정</span>
                    </div>
                    <div>
                      <strong>{job?.marks.length || 0}</strong>
                      <span>표시 음절</span>
                    </div>
                  </div>
                  {meaningsPending > 0 && (
                    <div className="meaning-notice">
                      <Info size={14} />
                      <span>
                        뜻 선택 대기 {meaningsPending}개 · 장단 판정과
                        별도입니다.
                      </span>
                    </div>
                  )}
                  {job && (
                    <>
                      <div className="review-search">
                        <Search size={14} />
                        <input
                          aria-label="검토 항목 검색"
                          placeholder="표현 또는 미확정 사유 검색"
                          value={query}
                          onChange={(e) => setQuery(e.target.value)}
                        />
                      </div>
                      <div className="review-filters">
                        {[
                          ["all", "전체"],
                          ["long", "장음"],
                          ["none", "표시 없음"],
                          ["unresolved", "장단 미확정"],
                          ["meaning", "뜻 검토"],
                        ].map(([v, l]) => (
                          <button
                            key={v}
                            className={filter === v ? "active" : ""}
                            onClick={() => setFilter(v)}
                          >
                            {l}
                          </button>
                        ))}
                      </div>
                      <label className="group-switch">
                        <input
                          type="checkbox"
                          checked={grouped}
                          onChange={(e) => setGrouped(e.target.checked)}
                        />
                        같은 표현 모아 보기
                      </label>
                    </>
                  )}
                  {running && (
                    <div className="analysis-progress">
                      <div>
                        <LoaderCircle size={14} className="spin" />
                        {job?.analysis.message}
                      </div>
                      <progress
                        value={job?.analysis.done || 0}
                        max={job?.analysis.total || 1}
                      />
                      <span>
                        {job?.analysis.done} / {job?.analysis.total}개 표현
                      </span>
                    </div>
                  )}
                  {job?.analysis.state === "waiting_key" && (
                    <div className="dictionary-warning">
                      <Info size={17} />
                      <strong>사전 연결을 기다리고 있어요</strong>
                      <p>
                        공식 발음 근거를 확인하려면 사전 인증키가 필요합니다.
                        미확정 항목을 확인하거나 직접 표시할 수 있어요.
                      </p>
                      <button onClick={() => setHelp(true)}>
                        연결 방법 보기 <ArrowRight size={13} />
                      </button>
                    </div>
                  )}
                  {!job ? (
                    <div className="review-empty">
                      <BookOpen size={29} />
                      <h4>대본을 만나면 시작할게요</h4>
                      <p>
                        장음과 발음 근거를
                        <br />
                        이곳에서 함께 확인할 수 있어요.
                      </p>
                    </div>
                  ) : filtered.length === 0 ? (
                    <div className="review-empty">
                      <CheckCheck size={29} />
                      <h4>
                        {occurrences.length
                          ? "해당하는 항목이 없어요"
                          : "분석을 시작해 보세요"}
                      </h4>
                      <p>
                        {occurrences.length
                          ? "필터나 검색어를 바꿔 보세요."
                          : "전체 어휘를 확인하고 결과를 모아드릴게요."}
                      </p>
                    </div>
                  ) : (
                    <div className="occurrence-list">
                      {filtered.map((o) => (
                        <div
                          key={o.id}
                          className={`occurrence ${active === o.id ? "active" : ""}`}
                        >
                          <button
                            className="occurrence-heading"
                            aria-expanded={active === o.id}
                            aria-controls={`detail-${o.id}`}
                            onClick={() => {
                              const opening = active !== o.id;
                              setActive(opening ? o.id : null);
                              setFocusedOccurrenceId(opening ? o.id : null);
                              setPage(o.page);
                            }}
                          >
                            <span>
                              <strong>{o.text}</strong>
                              <small>
                                {o.lemma !== o.text ? o.lemma + " · " : ""}
                                {o.page}쪽
                                {grouped
                                  ? ` · ${groupMembers(o).length}회`
                                  : ""}
                              </small>
                            </span>
                            <span className={`result-badge ${o.status}`}>
                              {o.source === "manual"
                                ? "직접 수정"
                                : o.status === "long"
                                  ? "장음"
                                  : o.status === "none"
                                    ? "표시 없음"
                                    : "미확정"}
                            </span>
                          </button>
                          <p>{o.reason}</p>
                          {o.meaning_status === "unresolved" && (
                            <span className="meaning-badge">
                              뜻 선택 미확정
                            </span>
                          )}
                          {o.evidence_type?.includes("rule") && (
                            <span className="rule-badge">규칙 적용 근거</span>
                          )}
                          {active === o.id && (
                            <div
                              className="occurrence-details"
                              id={`detail-${o.id}`}
                            >
                              <div className="source-locations">
                                {(grouped ? groupMembers(o) : [o]).map(
                                  (member, i) => (
                                    <button
                                      key={member.id}
                                      onClick={() => {
                                        setFocusedOccurrenceId(member.id);
                                        setPage(member.page);
                                        setMobileTab("pdf");
                                      }}
                                    >
                                      <Focus size={12} />
                                      {grouped && groupMembers(o).length > 1
                                        ? `${i + 1}번째 · `
                                        : "원문 위치 보기 · "}
                                      {member.page}쪽
                                    </button>
                                  ),
                                )}
                              </div>
                              {o.context && (
                                <blockquote>{o.context}</blockquote>
                              )}
                              {o.morphology?.lemma &&
                                o.morphology.lemma !== o.text && (
                                  <div className="morphology-note">
                                    원문 {o.text} → 기본형 {o.morphology.lemma}
                                    {o.morphology.particle
                                      ? ` + 조사 ${o.morphology.particle}`
                                      : ""}
                                    <br />
                                    원문 형태의 발음 근거를 확인합니다.
                                  </div>
                                )}
                              {o.evidence_candidates &&
                                o.evidence_candidates.length > 1 && (
                                  <div className="consensus-note">
                                    조회 후보 {o.evidence_candidates.length}개의
                                    장음 위치가 일치합니다. 특정 뜻을 자동
                                    선택한 결과가 아닙니다.
                                  </div>
                                )}
                              {o.applied_rules?.map((rule) => (
                                <a
                                  className="rule-source"
                                  href={rule.url}
                                  key={rule.id}
                                  target="_blank"
                                  rel="noreferrer"
                                >
                                  <Link2 size={12} />
                                  {rule.label}
                                </a>
                              ))}
                              {o.candidates.map((c) => (
                                <div className="candidate" key={c.id}>
                                  <div>
                                    <strong>
                                      {c.word || c.lemma || o.lemma}
                                    </strong>
                                    <span>
                                      {c.hanja || String(c.origin || "")}
                                    </span>
                                  </div>
                                  <b>표제어 [{pronunciation(c)}]</b>
                                  {c.inflections
                                    ?.filter((form) => form.form === o.text)
                                    .map((form, i) => (
                                      <b key={i} className="surface-reading">
                                        원문 [{form.pronunciations.join(" / ")}]
                                      </b>
                                    ))}
                                  <p>{c.definition || c.meaning}</p>
                                  <div className="candidate-actions">
                                    {(c.url || c.source_url) && (
                                      <a
                                        href={c.url || c.source_url}
                                        target="_blank"
                                        rel="noreferrer"
                                      >
                                        <Link2 size={12} />
                                        사전 근거
                                      </a>
                                    )}
                                    <button
                                      disabled={saving || running}
                                      onClick={() => void choose(o, c)}
                                    >
                                      {o.selected_candidate_id === c.id ? (
                                        <>
                                          <Check size={12} />
                                          선택됨
                                        </>
                                      ) : (
                                        "이 의미 선택"
                                      )}
                                    </button>
                                  </div>
                                </div>
                              ))}
                              <label className="memo-label">
                                검토 메모
                                <textarea
                                  defaultValue={o.note || ""}
                                  key={o.id + o.note}
                                  placeholder="이 표현에 관한 메모"
                                  onBlur={(e) => {
                                    if (job && e.target.value !== o.note)
                                      void patch({
                                        ...copy(job),
                                        occurrences: job.occurrences.map((x) =>
                                          x.id === o.id
                                            ? { ...x, note: e.target.value }
                                            : x,
                                        ),
                                      });
                                  }}
                                />
                              </label>
                            </div>
                          )}
                        </div>
                      ))}
                    </div>
                  )}
                </div>
              )}
              <div className="dictionary-status">
                <span className={configured ? "connected" : "waiting"}>
                  <span />
                  {configured
                    ? "표준국어대사전 연결 설정됨"
                    : "표준국어대사전 연결 대기"}
                </span>
                <button
                  aria-label="사전 연결 안내"
                  onClick={() => setHelp(true)}
                >
                  <Info size={14} />
                </button>
              </div>
            </aside>
          </div>
          <div className="workspace-statusbar">
            <span>
              <span className={`status-dot ${job ? "ready" : ""}`} />
              {running ||
              ["error", "cancelled"].includes(job?.analysis.state || "")
                ? job?.analysis.message
                : job?.analysis.state === "waiting_key"
                  ? "사전 인증키 미설정 · 자동 판정 대기"
                  : job?.analysis.state === "complete"
                    ? job.analysis.message
                    : job
                      ? "대본을 불러왔습니다"
                      : "준비됐어요. 첫 대본을 올려주세요."}
            </span>
            {job ? (
              <button
                className="delete-job"
                disabled={running || saving}
                onClick={() => void removeJob()}
              >
                <Trash2 size={12} />
                작업 삭제
              </button>
            ) : (
              <span>한 번의 표시, 더 편안한 낭독</span>
            )}
          </div>
        </section>
        <section className="benefits">
          <div>
            <span className="benefit-icon">
              <FileText size={20} strokeWidth={1.5} />
            </span>
            <div>
              <h3>대본은 원래 모습 그대로</h3>
              <p>글꼴과 문장, 페이지 배치를 유지해요.</p>
            </div>
          </div>
          <div>
            <span className="benefit-icon">
              <Highlighter size={20} strokeWidth={1.5} />
            </span>
            <div>
              <h3>읽는 데 필요한 만큼만</h3>
              <p>길게 읽는 음절 위에 은은한 원형 표시.</p>
            </div>
          </div>
          <div>
            <span className="benefit-icon">
              <BookOpen size={20} strokeWidth={1.5} />
            </span>
            <div>
              <h3>근거를 확인하는 장음</h3>
              <p>공식 사전 발음과 미확정 항목을 구분해요.</p>
            </div>
          </div>
        </section>
      </main>
      <footer className="site-footer">
        <span>
          소리<span className="brand-dot">.</span>{" "}
          <small>당신의 낭독에, 작은 자신감.</small>
        </span>
        <button onClick={() => setHelp(true)}>
          지원 범위 및 이용 안내 <ArrowRight size={12} />
        </button>
      </footer>
      <input
        ref={input}
        className="visually-hidden"
        type="file"
        accept="application/pdf,.pdf"
        aria-label="PDF 업로드"
        onChange={(e) => {
          if (e.target.files?.[0]) void upload(e.target.files[0]);
        }}
      />
      {notice && (
        <div className="toast" role="status">
          <Check size={16} />
          {notice}
        </div>
      )}
      {help && (
        <div className="modal-backdrop" onClick={() => setHelp(false)}>
          <section
            className="guide-modal"
            role="dialog"
            aria-modal="true"
            aria-labelledby="guide-title"
            onClick={(e) => e.stopPropagation()}
          >
            <button
              className="modal-close icon-button"
              aria-label="가이드 닫기"
              onClick={() => setHelp(false)}
            >
              <X size={19} />
            </button>
            <span className="guide-icon">
              <BookOpen size={26} />
            </span>
            <span className="eyebrow">A GUIDE TO SORI</span>
            <h2 id="guide-title">더 편안한 낭독을 준비하세요.</h2>
            <div className="guide-step">
              <b>01</b>
              <div>
                <h3>텍스트형 PDF를 올려주세요</h3>
                <p>
                  최대 20MB, 100페이지의 대본을 지원합니다. PDF를 올리면 사전 연결 상태에 따라 자동 분석이 시작됩니다. 스캔 페이지의 OCR은 아직 지원하지 않습니다.
                </p>
              </div>
            </div>
            <div className="guide-step">
              <b>02</b>
              <div>
                <h3>장음을 분석하고 검토하세요</h3>
                <p>
                  공식 사전의 검색·상세 발음 근거를 사용합니다. 뜻이나 활용이
                  모호하면 미확정으로 남기며, 근거가 없는 항목은 자동 표시하지
                  않습니다.
                </p>
              </div>
            </div>
            <div className="guide-step">
              <b>03</b>
              <div>
                <h3>내 방식으로 표시하고 저장하세요</h3>
                <p>
                  직접 표시 모드에서 음절을 클릭해 원을 추가하고, 위치와 크기를
                  조절하세요. 수동 수정은 검증 기록에 구분해 남습니다.
                </p>
              </div>
            </div>
            <div className="connection-help">
              <h3>
                <Settings2 size={16} />
                사전 연결 안내
              </h3>
              <p>
                {configured ? "공식 사전의 발음과 원문 음절을 연결해 표시합니다. 장단 판정과 뜻 선택은 별도로 기록하며, 미확정 항목을 직접 검토할 수 있습니다." : "자동 분석을 위한 사전 연결이 아직 준비되지 않았습니다. 직접 표시와 PDF 다운로드는 사용할 수 있습니다."}
              </p>
              <a
                href="https://stdict.korean.go.kr"
                target="_blank"
                rel="noreferrer"
              >
                표준국어대사전 바로가기 <ArrowRight size={13} />
              </a>
            </div>
            <p className="privacy-note">
              <ShieldCheck size={14} />
              파일은 임시 작업으로 보관되며, 작업 삭제 또는 마지막 접근 후
              2시간(정리 주기 약 1분)이 지나면 삭제됩니다. 서버 재시작 후 복구는
              지원하지 않습니다.
            </p>
            <button
              className="blue-button guide-done"
              onClick={() => setHelp(false)}
            >
              시작할게요 <ArrowRight size={15} />
            </button>
          </section>
        </div>
      )}
    </div>
  );
}
