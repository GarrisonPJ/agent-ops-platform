import { useEffect } from "react";
import { useParams, useNavigate } from "react-router-dom";
import { useDispatch, useSelector } from "react-redux";
import { CircleNotch, WarningCircle, Code } from "@phosphor-icons/react";
import { Group, Panel, Separator } from "react-resizable-panels";

import type { RootState, AppDispatch } from "../store";
import { togglePlay, resetPlayback } from "../store/trajectorySlice";
import { useGetTraceQuery } from "../services/api";
import TokenDashboard from "../components/TokenDashboard";
import StepInspector from "../components/StepInspector";
import ExecutionTimeline from "../components/ExecutionTimeline";
import { usePlaybackEngine } from "../hooks/usePlaybackEngine";

export default function TraceDetailPage() {
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();
  const dispatch = useDispatch<AppDispatch>();

  const {
    data: trace,
    isLoading,
    isError,
  } = useGetTraceQuery(id ?? "", { skip: !id });

  const { currentStepIndex, isPlaying, playbackSpeed } = useSelector(
    (state: RootState) => state.trajectory,
  );

  const steps = trace?.steps ?? [];
  const currentStep = steps[currentStepIndex] ?? null;
  const totalPlaybackMs = steps.length * 1000;

  const {
    elapsedMs,
    hasEverPlayed,
    handleManualSeek,
    handleProgressClick,
    handleCycleSpeed,
  } = usePlaybackEngine(steps, totalPlaybackMs, trace);

  const progress =
    steps.length > 0
      ? Math.min((elapsedMs / totalPlaybackMs) * 100, 100)
      : 0;

  const contextWindowLimit =
    steps.reduce(
      (max, s) => Math.max(max, s.context_window.limit),
      0,
    ) || 128000;

  // Keyboard shortcuts
  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if (
        e.target instanceof HTMLInputElement ||
        e.target instanceof HTMLTextAreaElement ||
        (e.target instanceof HTMLElement &&
          e.target.closest("[data-panel-resize-handle-id]"))
      ) {
        return;
      }
      switch (e.key) {
        case " ":
          e.preventDefault();
          dispatch(togglePlay());
          break;
        case "ArrowLeft":
          e.preventDefault();
          handleManualSeek(currentStepIndex - 1);
          break;
        case "ArrowRight":
          e.preventDefault();
          handleManualSeek(currentStepIndex + 1);
          break;
      }
    };
    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [dispatch, handleManualSeek, currentStepIndex]);

  // Reset playback state on unmount
  useEffect(() => {
    return () => {
      dispatch(resetPlayback());
    };
  }, [dispatch]);

  // Loading state
  if (isLoading) {
    return (
      <div className="flex items-center justify-center h-full">
        <CircleNotch className="w-8 h-8 text-accent animate-spin" />
      </div>
    );
  }

  // Error state
  if (isError || !trace) {
    return (
      <div className="flex items-center justify-center h-full">
        <div className="text-center">
          <WarningCircle className="w-12 h-12 text-destructive mx-auto mb-4" />
          <p className="text-fg-primary text-sm font-mono mb-2">
            Trace not found or failed to load
          </p>
          <button
            onClick={() => navigate("/traces")}
            className="text-accent text-sm hover:underline font-mono"
          >
            Back to Traces
          </button>
        </div>
      </div>
    );
  }

  return (
    <div className="h-full flex flex-col bg-bg-root">
      {/* ── Header: bold, framed, spacious ──────────────── */}
      <header className="border-b border-border bg-bg-card/30 shrink-0 z-10">
        <div className="w-full px-6 py-4 flex items-center justify-between gap-5 flex-wrap">
          {/* Breadcrumb + title */}
          <div className="flex items-center gap-3 min-w-0 flex-shrink">
            <button
              onClick={() => navigate("/traces")}
              className="text-sm font-mono text-fg-muted hover:text-fg-primary transition-colors rounded-sm focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-accent"
            >
              ← Back to Traces
            </button>
            <span className="text-lg font-bold tracking-tight text-fg-primary truncate border border-white/[0.10] rounded-lg px-3 py-1.5 bg-white/[0.02]">
              {trace.task}
            </span>
          </div>

          {/* Metrics row */}
          {trace.total_tokens != null && (
            <div className="flex items-center gap-3 flex-wrap ml-auto">
              <TokenDashboard
                inline
                totalTokens={trace.total_tokens}
                contextWindowPeak={trace.context_window_peak}
                steps={steps}
                contextWindowLimit={contextWindowLimit}
              />
              {trace.score != null && (
                <span className="inline-flex items-center gap-2.5 flex-shrink-0 bg-accent/10 border border-accent/25 rounded-lg px-4 py-2">
                  <span className="text-lg font-mono font-bold text-accent tabular-nums">
                    {trace.score.toFixed(2)}
                  </span>
                  <span className="text-[11px] font-mono font-semibold text-accent/80 uppercase tracking-wider">
                    Score
                  </span>
                </span>
              )}
              {trace.score_breakdown &&
                Object.entries(trace.score_breakdown)
                  .filter(([, v]) => typeof v === "number" && v !== 0)
                  .map(([key, val]) => (
                    <span
                      key={key}
                      className="inline-flex items-center flex-shrink-0 border border-white/[0.10] rounded-lg px-3 py-1.5 bg-white/[0.02]"
                    >
                      <span className="text-sm font-mono font-bold text-fg-primary tabular-nums">
                        {typeof val === "number"
                          ? (val > 0 ? "+" : "") + val.toFixed(2)
                          : String(val)}
                      </span>
                      <span className="text-[10px] font-mono text-fg-muted ml-2 uppercase tracking-wider">
                        {key.replace(/_/g, " ")}
                      </span>
                    </span>
                  ))}
            </div>
          )}
        </div>
      </header>

      {/* ── Main 2-Pane Workspace ───────────────────────── */}
      <main className="flex-1 overflow-hidden relative">
        <Group style={{ touchAction: "manipulation" }}>
          {/* 1. Stream / Timeline Panel */}
          <Panel
            defaultSize={50}
            minSize={30}
            className="flex flex-col bg-bg-card relative overflow-hidden"
          >
            <ExecutionTimeline
              steps={steps}
              currentStepIndex={currentStepIndex}
              isPlaying={isPlaying}
              hasEverPlayed={hasEverPlayed}
              progress={progress}
              onSeek={handleManualSeek}
              onProgressClick={handleProgressClick}
              onTogglePlay={() => dispatch(togglePlay())}
              onCycleSpeed={handleCycleSpeed}
              playbackSpeed={playbackSpeed}
            />
          </Panel>

          <Separator className="w-[1px] bg-border hover:bg-accent/50 hover:w-[2px] -ml-[1px] transition-[width] cursor-col-resize z-20" />

          {/* 2. Inspector Panel */}
          <Panel
            defaultSize={50}
            minSize={30}
            className="flex flex-col bg-bg-root"
          >
            <div className="flex items-center px-4 h-10 border-b border-border shrink-0 bg-bg-root sticky top-0 z-10">
              <h2 className="text-[10px] font-mono tracking-wider text-fg-muted uppercase flex items-center gap-2">
                <Code className="w-3.5 h-3.5" />
                Inspector
              </h2>
            </div>

            <StepInspector step={currentStep} />
          </Panel>
        </Group>
      </main>
    </div>
  );
}
