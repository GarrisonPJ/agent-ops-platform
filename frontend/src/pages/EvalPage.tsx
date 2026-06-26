import { useState, useEffect, useRef } from "react";
import { motion, AnimatePresence } from "framer-motion";
import {
  FlaskConical,
  Play,
  Download,
  GitCompare,
  Loader2,
  ChevronDown,
  Minus,
  Plus,
  AlertTriangle,
  Bug,
  Shield,
  Thermometer,
} from "lucide-react";
import { useNavigate, useSearchParams } from "react-router-dom";
import StatusBadge from "../components/StatusBadge";
import ErrorBanner from "../components/ErrorBanner";
import EmptyState from "../components/EmptyState";
import FailureDistributionChart from "../components/FailureDistributionChart";
import PolicyTimeline from "../components/PolicyTimeline";
import PolicyDetailPanel from "../components/PolicyDetailPanel";
import PolicyReviewQueue from "../components/PolicyReviewQueue";
import type {
  BenchmarkRanking,
  BenchmarkResponse,
  FailureReport,
  FailureEvidence,
  TrajectorySummary,
} from "../types";
import {
  useGetBenchmarksQuery,
  useRunBenchmarkMutation,
  useLazyExportTrajectoryQuery,
  useAnalyzeTrajectoryMutation,
  useGetTracesQuery,
  useGetPoliciesQuery,
  useGetActivePolicyQuery,
  useGetWarmupStatusQuery,
  useApprovePolicyMutation,
  useRejectPolicyMutation,
} from "../services/api";
import { formatTime } from "../lib/utils";
import { toast } from "../lib/toast";

/* ------------------------------------------------------------------ */
/*  Main page                                                         */
/* ------------------------------------------------------------------ */

export default function EvalPage() {
  const navigate = useNavigate();
  const [searchParams, setSearchParams] = useSearchParams();
  const view = searchParams.get("view") || "benchmark";

  const setView = (v: string) => {
    setSearchParams(v === "benchmark" ? {} : { view: v });
  };

  // ── mode & config state ──────────────────────────────────────────
  const [mode, setMode] = useState<"predefined" | "custom">("predefined");
  const [selectedTaskName, setSelectedTaskName] = useState("");
  const [customTask, setCustomTask] = useState("");
  const [runCount, setRunCount] = useState(3);
  const [isRunning, setIsRunning] = useState(false);
  const [elapsed, setElapsed] = useState(0);
  const [result, setResult] = useState<BenchmarkResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [openExportId, setOpenExportId] = useState<string | null>(null);

  // ── failures tab state ───────────────────────────────────────────
  const [selectedTrajectoryId, setSelectedTrajectoryId] = useState("");
  const [failureReport, setFailureReport] = useState<FailureReport | null>(null);
  const [failureLoading, setFailureLoading] = useState(false);
  const [failureError, setFailureError] = useState<string | null>(null);

  // ── policies tab state ───────────────────────────────────────────
  const [selectedPolicyId, setSelectedPolicyId] = useState<string | null>(null);

  // ── refs ─────────────────────────────────────────────────────────
  const startTimeRef = useRef(0);

  // ── API hooks ────────────────────────────────────────────────────
  const { data: benchmarks } = useGetBenchmarksQuery();
  const [runBenchmark] = useRunBenchmarkMutation();
  const [triggerExport] = useLazyExportTrajectoryQuery();
  const { data: tracesData } = useGetTracesQuery();
  const [analyzeTrajectory] = useAnalyzeTrajectoryMutation();

  const { data: policies, isLoading: policiesLoading } = useGetPoliciesQuery();
  const { data: activePolicy, isLoading: activePolicyLoading } =
    useGetActivePolicyQuery();
  const { data: warmupStatus } = useGetWarmupStatusQuery();
  const [approvePolicy, { isLoading: isApproving }] = useApprovePolicyMutation();
  const [rejectPolicy] = useRejectPolicyMutation();

  // ── auto-select first benchmark on load ──────────────────────────
  useEffect(() => {
    if (benchmarks && benchmarks.length > 0 && !selectedTaskName) {
      setSelectedTaskName(benchmarks[0].name);
    }
  }, [benchmarks, selectedTaskName]);

  // ── elapsed timer ────────────────────────────────────────────────
  useEffect(() => {
    if (!isRunning) {
      setElapsed(0);
      return;
    }
    startTimeRef.current = Date.now();
    const interval = setInterval(() => {
      setElapsed(Math.floor((Date.now() - startTimeRef.current) / 1000));
    }, 1000);
    return () => clearInterval(interval);
  }, [isRunning]);

  // ── close export dropdown on outside click ───────────────────────
  useEffect(() => {
    if (!openExportId) return;
    const handleClick = () => setOpenExportId(null);
    document.addEventListener("click", handleClick);
    return () => document.removeEventListener("click", handleClick);
  }, [openExportId]);

  // ── run handler ──────────────────────────────────────────────────
  const handleRun = async () => {
    setError(null);
    setResult(null);
    setIsRunning(true);

    try {
      const body: { task_name?: string; task?: string; n_runs: number } = {
        n_runs: runCount,
      };
      if (mode === "predefined") {
        body.task_name = selectedTaskName;
      } else {
        body.task = customTask;
      }
      const res = await runBenchmark(body).unwrap();
      setResult(res);
      toast.success(`Benchmark complete: ${res.completed}/${res.n_runs} runs`);
    } catch (err: unknown) {
      const msg =
        err instanceof Error
          ? err.message
          : typeof err === "object" && err !== null && "data" in err
            ? JSON.stringify((err as { data: unknown }).data)
            : "Benchmark run failed";
      setError(msg);
      toast.error(msg);
    } finally {
      setIsRunning(false);
    }
  };

  // ── compare handler (pass top 3 ranked trajectories) ────────────
  const handleCompare = () => {
    if (!result || result.rankings.length < 2) return;
    const top3 = result.rankings
      .slice(0, 3)
      .map((r) => r.trajectory_id)
      .join(",");
    navigate(`/compare?ids=${top3}`);
  };

  // ── export handler ───────────────────────────────────────────────
  const handleExport = async (
    trajectoryId: string,
    format: string,
  ) => {
    setOpenExportId(null);
    const res = await triggerExport({
      format,
      trajectory_id: trajectoryId,
    });
    if (res.error) {
      const msg =
        typeof res.error === "object" &&
        res.error !== null &&
        "data" in res.error
          ? String((res.error as { data: unknown }).data)
          : "Export failed";
      setError(msg);
      toast.error(msg);
      return;
    }
    if (!res.data) return;
    toast.success("Export downloaded");

    const url = URL.createObjectURL(res.data);
    const a = document.createElement("a");
    a.href = url;
    const ext = format === "rlhf_pair" ? "json" : "jsonl";
    a.download = `export.${ext}`;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);
  };

  // ── analysis handler ────────────────────────────────────────────
  const handleAnalyze = async (trajectoryId: string) => {
    if (!trajectoryId) return;
    setFailureLoading(true);
    setFailureError(null);
    setFailureReport(null);
    try {
      const res = await analyzeTrajectory({
        trajectory_id: trajectoryId,
      }).unwrap();
      setFailureReport(res);
    } catch (err: unknown) {
      const msg =
        err instanceof Error
          ? err.message
          : typeof err === "object" && err !== null && "data" in err
            ? JSON.stringify((err as { data: unknown }).data)
            : "Analysis failed";
      setFailureError(msg);
      toast.error(msg);
    } finally {
      setFailureLoading(false);
    }
  };

  // ── when selected trajectory changes, auto-analyze ──────────────
  useEffect(() => {
    if (view !== "failures" || !selectedTrajectoryId) return;
    handleAnalyze(selectedTrajectoryId);
  }, [selectedTrajectoryId, view]);

  // ── selected benchmark description ───────────────────────────────
  const selectedBenchmark = benchmarks?.find(
    (b) => b.name === selectedTaskName,
  );

  // ── which rows get best/worst borders ───────────────────────────
  const bestId = result?.best?.trajectory_id;
  const worstId = result?.worst?.trajectory_id;

  /* ------------------------------------------------------------------ */
  /*  Render                                                            */
  /* ------------------------------------------------------------------ */

  return (
    <div>
      {/* Page header */}
      <header className="border-b border-border px-6 py-4">
        <div className="max-w-6xl mx-auto flex items-center gap-3">
          <FlaskConical className="w-5 h-5 text-accent" />
          <h1 className="text-sm font-mono font-semibold text-fg-primary">
            Evaluations
          </h1>
        </div>
      </header>

      <main className="max-w-4xl mx-auto px-6 py-8 space-y-6">
        {/* ── Tab navigation ─────────────────────────────────────── */}
        <div className="flex items-center gap-1 bg-bg-card border border-border rounded-lg p-0.5">
          <button
            onClick={() => setView("benchmark")}
            className={`flex items-center gap-2 px-4 py-1.5 text-xs font-mono rounded-md transition-all duration-200 ${
              view === "benchmark"
                ? "bg-accent/15 text-accent border border-accent/30 font-semibold"
                : "text-fg-muted hover:text-fg-primary"
            }`}
          >
            <FlaskConical className="w-3.5 h-3.5" />
            Benchmark
          </button>
          <button
            onClick={() => setView("failures")}
            className={`flex items-center gap-2 px-4 py-1.5 text-xs font-mono rounded-md transition-all duration-200 ${
              view === "failures"
                ? "bg-accent/15 text-accent border border-accent/30 font-semibold"
                : "text-fg-muted hover:text-fg-primary"
            }`}
          >
            <Bug className="w-3.5 h-3.5" />
            Failures
          </button>
          <button
            onClick={() => setView("policies")}
            className={`flex items-center gap-2 px-4 py-1.5 text-xs font-mono rounded-md transition-all duration-200 ${
              view === "policies"
                ? "bg-accent/15 text-accent border border-accent/30 font-semibold"
                : "text-fg-muted hover:text-fg-primary"
            }`}
          >
            <Shield className="w-3.5 h-3.5" />
            Policies
          </button>
        </div>

        {/* ── Benchmark tab content ───────────────────────────────── */}
        {view === "benchmark" && (
          <>
            {/* Stats cards */}
            <div className="grid grid-cols-3 gap-5">
              {/* Success Rate — blue accent */}
              <div className="bg-bg-card border border-border rounded-xl p-5">
                <div className="text-xs font-mono text-fg-muted mb-1">SUCCESS RATE</div>
                <div className="text-3xl font-semibold text-accent">
                  {result ? `${((result.completed / result.n_runs) * 100).toFixed(1)}%` : "--"}
                </div>
              </div>
              {/* Best Score — amber accent, elevated */}
              <div className="bg-accent-amber/5 border border-accent-amber/20 rounded-xl p-5">
                <div className="text-xs font-mono text-accent-amber mb-1">BEST SCORE</div>
                <div className="text-3xl font-semibold text-accent-amber">
                  {result?.best ? result.best.score.toFixed(2) : "--"}
                </div>
              </div>
              {/* Worst Score — low profile */}
              <div className="bg-bg-card border border-border rounded-xl p-5">
                <div className="text-xs font-mono text-fg-muted mb-1">WORST SCORE</div>
                <div className="text-3xl font-semibold text-fg-primary">
                  {result?.worst ? result.worst.score.toFixed(2) : "--"}
                </div>
              </div>
            </div>

            {/* ──────────────────────────────────────────────────────── */}
            {/*  a) Configuration zone                                  */}
            {/* ──────────────────────────────────────────────────────── */}
        <motion.div
          initial={{ opacity: 0, y: 12 }}
          animate={{ opacity: 1, y: 0 }}
          className="bg-bg-card border border-border rounded-lg p-6 space-y-5"
        >
          {/* Mode toggle */}
          <div className="flex items-center gap-4">
            <span className="text-xs font-mono text-fg-muted uppercase tracking-wider">
              Task Mode
            </span>
            <div className="flex bg-bg-root rounded-lg p-0.5 border border-border">
              <button
                onClick={() => setMode("predefined")}
                className={`px-4 py-1.5 text-xs font-mono rounded-md transition-all duration-200 ${
                  mode === "predefined"
                    ? "bg-accent/15 text-accent border border-accent/30 font-semibold"
                    : "text-fg-muted hover:text-fg-primary"
                }`}
              >
                Predefined Task
              </button>
              <button
                onClick={() => setMode("custom")}
                className={`px-4 py-1.5 text-xs font-mono rounded-md transition-all duration-200 ${
                  mode === "custom"
                    ? "bg-accent/15 text-accent border border-accent/30 font-semibold"
                    : "text-fg-muted hover:text-fg-primary"
                }`}
              >
                Custom Task
              </button>
            </div>
          </div>

          {/* Predefined: dropdown */}
          {mode === "predefined" && (
            <div className="space-y-2">
              <label className="block text-xs font-mono text-fg-muted uppercase tracking-wider">
                Benchmark Task
              </label>
              <div className="relative">
                <select
                  value={selectedTaskName}
                  onChange={(e) => setSelectedTaskName(e.target.value)}
                  className="w-full appearance-none bg-bg-root border border-border rounded-lg px-3 py-2 pr-8 text-sm text-fg-primary font-mono outline-none focus:border-accent/50"
                >
                  {benchmarks?.map((b) => (
                    <option key={b.name} value={b.name}>
                      {b.name} — {b.description}
                    </option>
                  ))}
                </select>
                <ChevronDown className="absolute right-3 top-1/2 -translate-y-1/2 w-3.5 h-3.5 text-fg-muted pointer-events-none" />
              </div>
              {selectedBenchmark && (
                <p className="text-xs text-fg-muted font-mono mt-1">
                  {selectedBenchmark.task}
                </p>
              )}
            </div>
          )}

          {/* Custom: textarea */}
          {mode === "custom" && (
            <div className="space-y-2">
              <label className="block text-xs font-mono text-fg-muted uppercase tracking-wider">
                Task Prompt
              </label>
              <textarea
                value={customTask}
                onChange={(e) => setCustomTask(e.target.value)}
                placeholder="Enter a custom task prompt..."
                rows={3}
                className="w-full bg-bg-root border border-border rounded-lg px-3 py-2 text-sm text-fg-primary placeholder-fg-muted/50 font-mono outline-none focus:border-accent/50 resize-none"
              />
            </div>
          )}

          {/* Run count + Run button */}
          <div className="flex items-center justify-between pt-2">
            <div className="flex items-center gap-3">
              <label className="text-xs font-mono text-fg-muted uppercase tracking-wider">
                Runs
              </label>
              <div className="inline-flex items-center rounded-lg border border-white/[0.08] bg-bg-card overflow-hidden">
                <button
                  type="button"
                  onClick={() => setRunCount(Math.max(1, runCount - 1))}
                  disabled={runCount <= 1}
                  className="w-7 h-7 flex items-center justify-center text-fg-muted hover:bg-white/[0.04] disabled:opacity-30 disabled:cursor-not-allowed transition-all"
                >
                  <Minus className="w-3 h-3" />
                </button>
                <div className="w-10 h-7 flex items-center justify-center text-sm font-mono text-fg-primary tabular-nums border-x border-white/[0.08]">
                  {runCount}
                </div>
                <button
                  type="button"
                  onClick={() => setRunCount(Math.min(10, runCount + 1))}
                  disabled={runCount >= 10}
                  className="w-7 h-7 flex items-center justify-center text-fg-muted hover:bg-white/[0.04] disabled:opacity-30 disabled:cursor-not-allowed transition-all"
                >
                  <Plus className="w-3 h-3" />
                </button>
              </div>
            </div>

            <button
              onClick={handleRun}
              disabled={isRunning || (mode === "custom" && !customTask.trim())}
              className="flex items-center gap-2 bg-accent text-bg-root font-semibold rounded-lg px-6 py-2 text-sm hover:brightness-110 active:scale-[0.97] transition-all disabled:opacity-50 disabled:cursor-not-allowed"
            >
              <Play className="w-4 h-4" />
              Run Benchmark
            </button>
          </div>
        </motion.div>

        {/* ──────────────────────────────────────────────────────── */}
        {/*  b) Progress zone                                       */}
        {/* ──────────────────────────────────────────────────────── */}
        <AnimatePresence>
          {isRunning && (
            <motion.div
              key="progress"
              initial={{ opacity: 0, y: 12 }}
              animate={{ opacity: 1, y: 0 }}
              exit={{ opacity: 0, y: -12 }}
              className="bg-bg-card border border-border rounded-lg p-6 flex items-center gap-4"
            >
              <Loader2 className="w-6 h-6 text-accent animate-spin" />
              <div>
                <p className="text-sm font-mono text-fg-primary">
                  Running benchmark...
                </p>
                <p className="text-xs font-mono text-fg-muted mt-1 tabular-nums">
                  Elapsed: {formatTime(elapsed)}
                </p>
              </div>
            </motion.div>
          )}
        </AnimatePresence>

        {/* ──────────────────────────────────────────────────────── */}
        {/*  Error                                                  */}
        {/* ──────────────────────────────────────────────────────── */}
        <AnimatePresence>
          {error && (
            <motion.div
              key="error"
              initial={{ opacity: 0, y: -8 }}
              animate={{ opacity: 1, y: 0 }}
              exit={{ opacity: 0, y: -8 }}
            >
              <ErrorBanner title="Error" message={error} />
            </motion.div>
          )}
        </AnimatePresence>

        {/* ──────────────────────────────────────────────────────── */}
        {/*  c) Results zone                                        */}
        {/* ──────────────────────────────────────────────────────── */}
        <AnimatePresence mode="wait">
          {result && !isRunning && (
            <motion.div
              key="results"
              initial={{ opacity: 0, y: 16 }}
              animate={{ opacity: 1, y: 0 }}
              exit={{ opacity: 0, y: -16 }}
              transition={{ duration: 0.25, ease: "easeOut" }}
              className="space-y-4"
            >
              {/* Summary bar */}
              <div className="bg-bg-card border border-border rounded-lg p-4 flex items-center justify-between">
                <div className="flex items-center gap-6">
                  <span className="text-sm font-mono text-accent font-semibold">
                    {result.completed}/{result.n_runs} completed
                  </span>
                  {result.best && (
                    <span className="text-xs font-mono text-fg-muted">
                      Best:{" "}
                      <span className="text-accent tabular-nums">
                        {result.best.score.toFixed(2)}
                      </span>
                    </span>
                  )}
                  {result.worst && (
                    <span className="text-xs font-mono text-fg-muted">
                      Worst:{" "}
                      <span className="text-destructive tabular-nums">
                        {result.worst.score.toFixed(2)}
                      </span>
                    </span>
                  )}
                </div>
                {result.rankings.length >= 2 && (
                  <button
                    onClick={handleCompare}
                    className="flex items-center gap-1.5 text-xs font-mono text-fg-muted hover:text-accent transition-colors"
                  >
                    <GitCompare className="w-3.5 h-3.5" />
                    Compare Top 3
                  </button>
                )}
              </div>

              {/* Rankings table */}
              <div className="bg-bg-card border border-border rounded-lg overflow-hidden">
                <table className="w-full">
                  <thead>
                    <tr className="border-b border-border">
                      <th className="text-left text-xs text-fg-muted font-semibold font-mono px-4 py-3 uppercase tracking-wider w-14">
                        Rank
                      </th>
                      <th className="text-left text-xs text-fg-muted font-semibold font-mono px-4 py-3 uppercase tracking-wider">
                        Trajectory ID
                      </th>
                      <th className="text-left text-xs text-fg-muted font-semibold font-mono px-4 py-3 uppercase tracking-wider w-24">
                        Score
                      </th>
                      <th className="text-left text-xs text-fg-muted font-semibold font-mono px-4 py-3 uppercase tracking-wider w-28">
                        Status
                      </th>
                      <th className="text-right text-xs text-fg-muted font-semibold font-mono px-4 py-3 uppercase tracking-wider w-48">
                        Actions
                      </th>
                    </tr>
                  </thead>
                  <tbody>
                    {result.rankings.map((row: BenchmarkRanking) => {
                      const isBest = row.trajectory_id === bestId;
                      const isWorst = row.trajectory_id === worstId;
                      return (
                        <ResultRow
                          key={row.trajectory_id}
                          row={row}
                          isBest={isBest}
                          isWorst={isWorst}
                          openExportId={openExportId}
                          onExportClick={(id) =>
                            setOpenExportId(
                              openExportId === id ? null : id,
                            )
                          }
                          onExportAction={(id, fmt) =>
                            handleExport(id, fmt)
                          }
                          onCompare={handleCompare}
                        />
                      );
                    })}
                  </tbody>
                </table>
              </div>
            </motion.div>
          )}
        </AnimatePresence>

        {/* Initial empty state (no results and not running) */}
        {!result && !isRunning && !error && (
          <motion.div
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
          >
            <EmptyState
              icon={FlaskConical}
              message="Configure and run a benchmark evaluation"
              description="Results will appear here after the benchmark completes"
            />
          </motion.div>
        )}
          </>
        )}

        {/* ── Failures tab content ────────────────────────────────── */}
        {view === "failures" && (
          <motion.div
            initial={{ opacity: 0, y: 12 }}
            animate={{ opacity: 1, y: 0 }}
            className="space-y-6"
          >
            {/* Trajectory selector */}
            <div className="bg-bg-card border border-border rounded-lg p-6 space-y-4">
              <div className="space-y-2">
                <label className="block text-xs font-mono text-fg-muted uppercase tracking-wider">
                  Select Trajectory
                </label>
                <div className="relative">
                  <select
                    value={selectedTrajectoryId}
                    onChange={(e) => setSelectedTrajectoryId(e.target.value)}
                    className="w-full appearance-none bg-bg-root border border-border rounded-lg px-3 py-2 pr-8 text-sm text-fg-primary font-mono outline-none focus:border-accent/50"
                  >
                    <option value="">-- Select a trajectory --</option>
                    {tracesData?.trajectories?.map((t: TrajectorySummary) => (
                      <option key={t.id} value={t.id}>
                        {t.id.substring(0, 8)}... — {t.task.substring(0, 60)}{t.task.length > 60 ? "..." : ""} ({t.status})
                      </option>
                    ))}
                  </select>
                  <ChevronDown className="absolute right-3 top-1/2 -translate-y-1/2 w-3.5 h-3.5 text-fg-muted pointer-events-none" />
                </div>
              </div>
            </div>

            {/* Analysis results */}
            {selectedTrajectoryId && (
              <>
                {/* Failure Distribution Chart */}
                <FailureDistributionChart
                  data={
                    failureReport
                      ? {
                          planning: failureReport.dimensions.planning ?? 0,
                          execution: failureReport.dimensions.execution ?? 0,
                          context: failureReport.dimensions.context ?? 0,
                          budget: failureReport.dimensions.budget ?? 0,
                        }
                      : null
                  }
                  loading={failureLoading}
                  error={failureError}
                />

                {/* Dominant failure type */}
                {failureReport && failureReport.dominant && (
                  <motion.div
                    initial={{ opacity: 0, y: 8 }}
                    animate={{ opacity: 1, y: 0 }}
                    className="bg-bg-card border border-border rounded-lg p-5 flex items-center gap-4"
                  >
                    <span className="flex items-center justify-center w-10 h-10 rounded-xl bg-accent-amber/10 ring-1 ring-accent-amber/20 flex-shrink-0">
                      <AlertTriangle className="w-5 h-5 text-accent-amber" />
                    </span>
                    <div>
                      <p className="text-xs font-mono text-fg-muted uppercase tracking-wider">
                        Dominant Failure Type
                      </p>
                      <p className="text-sm font-mono text-accent-amber font-semibold mt-0.5 capitalize">
                        {failureReport.dominant}
                      </p>
                    </div>
                  </motion.div>
                )}

                {/* Evidence list */}
                {failureReport && failureReport.evidence.length > 0 && (
                  <motion.div
                    initial={{ opacity: 0, y: 8 }}
                    animate={{ opacity: 1, y: 0 }}
                    className="bg-bg-card border border-border rounded-lg p-6 space-y-4"
                  >
                    <h3 className="text-xs font-mono text-fg-muted uppercase tracking-wider">
                      Failure Evidence ({failureReport.evidence.length})
                    </h3>
                    <div className="space-y-3">
                      {failureReport.evidence.map((ev: FailureEvidence, i: number) => (
                        <div
                          key={i}
                          className="bg-bg-root border border-border rounded-lg p-4 space-y-2"
                        >
                          <div className="flex items-center justify-between">
                            <div className="flex items-center gap-2">
                              <span
                                className="w-2 h-2 rounded-full"
                                style={{
                                  backgroundColor:
                                    ev.dimension === "planning"
                                      ? "#4b8cf7"
                                      : ev.dimension === "execution"
                                        ? "#ef4444"
                                        : ev.dimension === "context"
                                          ? "#f5a623"
                                          : "#2dd4bf",
                                }}
                              />
                              <span className="text-xs font-mono text-fg-muted uppercase">
                                {ev.dimension}
                              </span>
                              <span className="text-xs font-mono text-fg-subtle">
                                Step {ev.step_index}
                              </span>
                            </div>
                            <span
                              className="text-xs font-mono tabular-nums"
                              style={{
                                color:
                                  ev.severity >= 0.7
                                    ? "#ef4444"
                                    : ev.severity >= 0.4
                                      ? "#f5a623"
                                      : "#878d96",
                              }}
                            >
                              severity: {ev.severity.toFixed(2)}
                            </span>
                          </div>
                          <p className="text-sm text-fg-primary font-mono leading-relaxed">
                            {ev.reason}
                          </p>
                          {ev.details && (
                            <pre className="text-xs text-fg-muted font-mono bg-bg-card rounded p-2 overflow-x-auto max-h-24 overflow-y-auto schema-scroll">
                              {JSON.stringify(ev.details, null, 2)}
                            </pre>
                          )}
                        </div>
                      ))}
                    </div>
                  </motion.div>
                )}

                {/* Empty state when analyzed but no evidence */}
                {failureReport && failureReport.evidence.length === 0 && (
                  <EmptyState
                    icon={Bug}
                    message="No failure evidence found"
                    description="This trajectory completed without detected failures"
                  />
                )}
              </>
            )}

            {/* Empty state when no trajectory selected */}
            {!selectedTrajectoryId && (
              <EmptyState
                icon={Bug}
                message="Select a trajectory to analyze"
                description="Pick a trajectory from the dropdown above to view failure analysis"
              />
            )}
          </motion.div>
        )}

        {/* ── Policies tab content ────────────────────────────────── */}
        {view === "policies" && (
          <motion.div
            initial={{ opacity: 0, y: 12 }}
            animate={{ opacity: 1, y: 0 }}
            className="space-y-6"
          >
            {/* Warmup indicator (when no active policy) */}
            {!activePolicyLoading &&
              !activePolicy &&
              warmupStatus &&
              !warmupStatus.ready && (
                <motion.div
                  initial={{ opacity: 0, y: 8 }}
                  animate={{ opacity: 1, y: 0 }}
                  className="bg-bg-card border border-border rounded-lg p-5 flex items-center gap-4"
                >
                  <span className="flex items-center justify-center w-10 h-10 rounded-xl bg-accent-amber/10 ring-1 ring-accent-amber/20 flex-shrink-0">
                    <Thermometer className="w-5 h-5 text-accent-amber" />
                  </span>
                  <div>
                    <p className="text-xs font-mono text-fg-muted uppercase tracking-wider">
                      Warming Up
                    </p>
                    <p className="text-sm font-mono text-accent-amber font-semibold mt-0.5">
                      {warmupStatus.total_trajectories}/
                      {warmupStatus.threshold} trajectories collected
                    </p>
                  </div>
                </motion.div>
              )}

            {/* Timeline */}
            <PolicyTimeline
              policies={policies ?? []}
              selectedId={selectedPolicyId}
              onSelect={(id) => setSelectedPolicyId(id)}
              loading={policiesLoading}
            />

            {/* Review queue */}
            <PolicyReviewQueue />

            {/* Detail panel */}
            {selectedPolicyId && policies ? (
              <PolicyDetailPanel
                policy={
                  policies.find(
                    (p) => p.version_id === selectedPolicyId,
                  ) ?? null
                }
                loading={false}
                onApprove={(id) => approvePolicy(id).unwrap().then(() => {
                  toast.success("Policy approved and activated");
                }).catch(() => {
                  toast.error("Failed to approve policy");
                })}
                onReject={(id) => {
                  const r = prompt("Reason for rejection (optional):");
                  rejectPolicy({ version_id: id, reason: r || "" })
                    .unwrap()
                    .then(() => toast.success("Policy rejected"))
                    .catch(() => toast.error("Failed to reject policy"));
                }}
                approving={isApproving}
              />
            ) : (
              <PolicyDetailPanel policy={null} loading={false} />
            )}
          </motion.div>
        )}
      </main>
    </div>
  );
}

/* ------------------------------------------------------------------ */
/*  ResultRow — single table row with border styling + actions        */
/* ------------------------------------------------------------------ */

function ResultRow({
  row,
  isBest,
  isWorst,
  openExportId,
  onExportClick,
  onExportAction,
  onCompare,
}: {
  row: BenchmarkRanking;
  isBest: boolean;
  isWorst: boolean;
  openExportId: string | null;
  onExportClick: (id: string) => void;
  onExportAction: (id: string, format: string) => void;
  onCompare: () => void;
}) {
  const borderClass = isBest
    ? "border-l-accent-amber border-l-2"
    : isWorst
      ? "border-l-destructive border-l-2"
      : "";

  const FORMATS = [
    { key: "openai_sft", label: "SFT" },
    { key: "rlhf_pair", label: "RLHF Pair" },
    { key: "jsonl", label: "JSONL" },
  ] as const;

  return (
    <tr
      className={`border-b border-border last:border-b-0 transition-colors ${borderClass} ${
        isBest ? "bg-accent-amber/5" : isWorst ? "bg-error/5" : ""
      } hover:bg-bg-hover`}
    >
      <td className="px-4 py-3">
        <span className="text-sm font-mono text-fg-primary tabular-nums flex items-center gap-1.5">
          {row.rank}
          {row.rank === 1 && (
            <span className="text-[10px] font-semibold uppercase text-accent-amber bg-accent-amber/10 px-1 py-0.5 rounded">
              Best
            </span>
          )}
        </span>
      </td>
      <td className="px-4 py-3">
        <span className="text-sm font-mono text-fg-muted">
          {row.trajectory_id.substring(0, 8)}...
        </span>
      </td>
      <td className="px-4 py-3">
        <span
          className="text-sm text-accent-amber tabular-nums font-mono"
        >
          {row.score.toFixed(2)}
        </span>
      </td>
      <td className="px-4 py-3">
        <StatusBadge status={row.status} />
      </td>
      <td className="px-4 py-3 text-right">
        <div className="flex items-center justify-end gap-2">
          {/* Compare button */}
          <button
            onClick={onCompare}
            className="flex items-center gap-1 px-2.5 py-1.5 rounded text-xs font-mono text-fg-muted hover:text-accent hover:bg-accent/10 transition-colors"
            title="Compare top 3 trajectories"
          >
            <GitCompare className="w-3.5 h-3.5" />
            Compare
          </button>

          {/* Export dropdown */}
          <div className="relative">
            <button
              onClick={(e) => {
                e.stopPropagation();
                onExportClick(row.trajectory_id);
              }}
              className="flex items-center gap-1 px-2.5 py-1.5 rounded text-xs font-mono text-fg-muted hover:text-accent hover:bg-accent/10 transition-colors"
              title="Export trajectory"
            >
              <Download className="w-3.5 h-3.5" />
              Export
              <ChevronDown className="w-3 h-3" />
            </button>

            {openExportId === row.trajectory_id && (
              <div
                className="absolute right-0 top-full mt-1 z-50 bg-bg-card border border-border rounded-lg shadow-lg overflow-hidden min-w-[130px]"
                onClick={(e) => e.stopPropagation()}
              >
                {FORMATS.map((fmt) => (
                  <button
                    key={fmt.key}
                    onClick={() =>
                      onExportAction(row.trajectory_id, fmt.key)
                    }
                    className="w-full text-left px-3 py-2 text-xs font-mono text-fg-muted hover:text-fg-primary hover:bg-bg-hover transition-colors"
                  >
                    {fmt.label}
                  </button>
                ))}
              </div>
            )}
          </div>
        </div>
      </td>
    </tr>
  );
}
