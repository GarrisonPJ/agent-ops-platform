import {
  ArrowLeft,
  ArrowRight,
  CheckCircle,
  Clock,
  Code,
  Lightning,
  Play,
  Stop,
  WarningCircle,
} from "@phosphor-icons/react";
import { KeyboardEvent, useEffect, useMemo, useState } from "react";
import { Link, useNavigate, useParams, useSearchParams } from "react-router-dom";
import EmptyState from "../components/EmptyState";
import ErrorBanner from "../components/ErrorBanner";
import LifecycleBadge from "../components/LifecycleBadge";
import LoadingSkeleton from "../components/LoadingSkeleton";
import { useRunStream } from "../hooks/useRunStream";
import {
  formatDuration,
  formatScore,
  getApiErrorMessage,
  totalTokens,
} from "../lib/phase1Format";
import {
  compareAnalysis,
  compareMetric,
  compareSteps,
  type ComparisonStatus,
  type MetricComparison,
  type StepComparison,
} from "../lib/comparison";
import { toast } from "../lib/toast";
import {
  useActivatePolicyMutation,
  useCancelRunMutation,
  useGetExperimentQuery,
  useGetRunAnalysisQuery,
  useGetRunQuery,
  useRejectPolicyMutation,
  useReplayPolicyMutation,
} from "../services/experimentsApi";
import type {
  EventEnvelope,
  Policy,
  Run,
  RunStatus,
  StepCompletedPayload,
} from "../types/phase1";

const TABS = ["trace", "analysis", "improve"] as const;
type Tab = (typeof TABS)[number];

const PENDING_STATUSES: RunStatus[] = ["queued", "claimed", "running", "cancelling"];

function isTab(value: string | null): value is Tab {
  return TABS.includes(value as Tab);
}

function formatFailureType(value: string): string {
  return value.replace(/_/g, " ");
}

function effectiveStatus(run: Run, events: EventEnvelope[]): RunStatus {
  const last = events[events.length - 1];
  if (!last) return run.status;
  if (last.type === "run_completed") return "succeeded";
  if (last.type === "run_failed") return "failed";
  if (last.type === "run_cancelled") return "cancelled";
  if (last.type === "run_started" && PENDING_STATUSES.includes(run.status)) return "running";
  return run.status;
}

function stepPayload(event: EventEnvelope): StepCompletedPayload {
  const payload = event.payload as Partial<StepCompletedPayload>;
  return {
    index: typeof payload.index === "number" ? payload.index : event.sequence,
    decision_summary:
      typeof payload.decision_summary === "string"
        ? payload.decision_summary
        : "No decision summary was recorded.",
    tool_call:
      payload.tool_call && typeof payload.tool_call === "object"
        ? payload.tool_call
        : null,
    observation: typeof payload.observation === "string" ? payload.observation : "",
    latency_ms: typeof payload.latency_ms === "number" ? payload.latency_ms : 0,
    token_prompt: typeof payload.token_prompt === "number" ? payload.token_prompt : null,
    token_completion:
      typeof payload.token_completion === "number" ? payload.token_completion : null,
    context_window:
      payload.context_window && typeof payload.context_window === "object"
        ? payload.context_window
        : null,
  };
}

function Metric({ label, value, tone = "default" }: { label: string; value: string; tone?: "default" | "success" | "error" }) {
  const color = tone === "success" ? "text-success" : tone === "error" ? "text-error" : "text-fg-primary";
  return (
    <div className="rounded-md border border-border bg-bg-card px-4 py-3 shadow-inner-glow">
      <p className="font-mono text-[10px] uppercase tracking-wider text-fg-subtle">{label}</p>
      <p className={`mt-1 font-mono text-lg font-semibold ${color}`}>{value}</p>
    </div>
  );
}

function TracePanel({
  events,
  connection,
  connectionError,
  terminal,
}: {
  events: EventEnvelope[];
  connection: string;
  connectionError: string | null;
  terminal: boolean;
}) {
  const stepEvents = events.filter((event) => event.type === "step_completed");
  const outputEvents = events.filter((event) => event.type === "process_output");

  if (!events.length && !terminal) return <LoadingSkeleton variant="detail" />;

  return (
    <div className="space-y-4">
      {connectionError && connection === "reconnecting" ? (
        <div role="status" className="flex items-center gap-2 rounded-md border border-accent-amber/20 bg-accent-amber/[0.06] px-4 py-3 text-sm text-accent-amber">
          <Clock className="h-4 w-4" /> Reconnecting from the last persisted event…
        </div>
      ) : null}

      {stepEvents.length === 0 && terminal ? (
        <EmptyState
          icon={Code}
          message="No execution steps"
          description="The run ended before the runner persisted a tool step. Review the run error above."
        />
      ) : null}

      <ol className="space-y-3" aria-label="Run event timeline">
        {stepEvents.map((event) => {
          const step = stepPayload(event);
          return (
            <li key={event.sequence} className="relative rounded-md border border-border bg-bg-card p-4 shadow-inner-glow sm:p-5">
              <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
                <div className="flex min-w-0 gap-3">
                  <span className="flex h-7 w-7 shrink-0 items-center justify-center rounded-md border border-accent/20 bg-accent/10 font-mono text-xs font-semibold text-accent">
                    {step.index + 1}
                  </span>
                  <div className="min-w-0">
                    <p className="text-sm font-medium leading-6 text-fg-primary">{step.decision_summary}</p>
                    {step.tool_call ? (
                      <div className="mt-3 rounded-md border border-border bg-bg-root/70 p-3">
                        <div className="flex flex-wrap items-center gap-2">
                          <Lightning className="h-4 w-4 text-accent-amber" weight="fill" />
                          <code className="font-mono text-xs font-semibold text-accent-amber">{step.tool_call.name}</code>
                        </div>
                        <pre className="schema-scroll mt-2 overflow-x-auto font-mono text-[11px] leading-5 text-fg-muted">
                          {JSON.stringify(step.tool_call.arguments, null, 2)}
                        </pre>
                      </div>
                    ) : null}
                  </div>
                </div>
                <span className="shrink-0 font-mono text-[11px] text-fg-subtle">{formatDuration(step.latency_ms)}</span>
              </div>
              {step.observation ? (
                <div className="mt-4 border-t border-border pt-4">
                  <p className="font-mono text-[10px] uppercase tracking-wider text-fg-subtle">Observation</p>
                  <p className="mt-2 whitespace-pre-wrap text-sm leading-6 text-fg-muted">{step.observation}</p>
                </div>
              ) : null}
              <div className="mt-4 flex flex-wrap gap-4 font-mono text-[10px] text-fg-subtle">
                <span>event #{event.sequence}</span>
                {step.token_prompt != null ? <span>{step.token_prompt} prompt tokens</span> : null}
                {step.token_completion != null ? <span>{step.token_completion} completion tokens</span> : null}
              </div>
            </li>
          );
        })}
      </ol>

      {outputEvents.length > 0 ? (
        <details className="rounded-md border border-border bg-bg-card p-4">
          <summary className="cursor-pointer text-sm font-semibold">Process output</summary>
          <pre className="schema-scroll mt-3 max-h-64 overflow-auto whitespace-pre-wrap font-mono text-xs leading-5 text-fg-muted">
            {outputEvents.map((event) => String(event.payload.line ?? event.payload.output ?? "")).join("\n")}
          </pre>
        </details>
      ) : null}
    </div>
  );
}

function PolicyDiff({ policy, baseline }: { policy: Policy; baseline: Run }) {
  const baseMaxSteps = baseline.evaluation_spec.policy?.max_steps ?? 6;
  return (
    <div className="overflow-hidden rounded-md border border-border bg-bg-root font-mono text-xs shadow-inner-glow">
      <div className="border-b border-border bg-white/[0.025] px-4 py-2.5 text-[10px] uppercase tracking-wider text-fg-subtle">
        Candidate policy diff
      </div>
      <div className="space-y-5 p-4 sm:p-5">
        <div>
          <p className="mb-2 text-fg-subtle">instruction_patch</p>
          {policy.patch.instruction_patch.map((instruction) => (
            <p key={instruction} className="border-l-2 border-success/50 bg-success/[0.06] px-3 py-2 leading-5 text-success">
              + {instruction}
            </p>
          ))}
        </div>
        <div>
          <p className="mb-2 text-fg-subtle">tool_priority</p>
          {Object.entries(policy.patch.tool_priority).map(([tool, priority]) => (
            <p key={tool} className="grid grid-cols-[minmax(0,1fr)_auto] gap-3 border-l-2 border-success/50 bg-success/[0.06] px-3 py-2 text-success">
              <span className="truncate">+ {tool}</span><span>{priority.toFixed(1)}</span>
            </p>
          ))}
        </div>
        <div className="grid grid-cols-[minmax(0,1fr)_auto] gap-3">
          <span className="text-fg-subtle">max_steps</span>
          <span><span className="text-error line-through">{baseMaxSteps ?? "—"}</span> <span className="text-success">→ {policy.patch.max_steps}</span></span>
        </div>
      </div>
    </div>
  );
}

const STATUS_RANK: Record<ComparisonStatus, number> = { regressed: 0, improved: 1, unchanged: 2 };

function statusLabel(status: ComparisonStatus) {
  return status[0].toUpperCase() + status.slice(1);
}

function statusClasses(status: ComparisonStatus) {
  if (status === "regressed") return "border-error/30 bg-error/[0.07] text-error";
  if (status === "improved") return "border-success/25 bg-success/[0.06] text-success";
  return "border-border bg-white/[0.025] text-fg-muted";
}

function formatSigned(value: number | null, formatter: (value: number) => string) {
  if (value == null) return "—";
  return `${value > 0 ? "+" : ""}${formatter(value)}`;
}

function ComparisonMetricRow({
  label,
  comparison,
  formatter,
}: {
  label: string;
  comparison: MetricComparison;
  formatter: (value: number) => string;
}) {
  return (
    <div className={`rounded-md border p-4 ${statusClasses(comparison.status)}`}>
      <div className="flex flex-wrap items-center justify-between gap-2">
        <p className="font-mono text-[10px] uppercase tracking-wider text-fg-subtle">{label}</p>
        <span className="rounded-full border px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wide">
          {statusLabel(comparison.status)}
        </span>
      </div>
      <div className="mt-3 grid grid-cols-[1fr_auto_1fr] items-baseline gap-2 font-mono">
        <span>{comparison.before == null ? "—" : formatter(comparison.before)}</span>
        <ArrowRight className="h-4 w-4 text-fg-subtle" />
        <span className="text-right">{comparison.after == null ? "—" : formatter(comparison.after)}</span>
      </div>
      <p className="mt-2 text-right font-mono text-xs text-fg-muted">{formatSigned(comparison.delta, formatter)}</p>
    </div>
  );
}

function sortComparisons(entries: [string, MetricComparison][]) {
  return [...entries].sort(
    ([leftKey, left], [rightKey, right]) =>
      STATUS_RANK[left.status] - STATUS_RANK[right.status] || leftKey.localeCompare(rightKey),
  );
}

function AnalysisComparisonList({
  title,
  comparisons,
  emptyMessage,
}: {
  title: string;
  comparisons: Record<string, MetricComparison>;
  emptyMessage: string;
}) {
  const entries = sortComparisons(Object.entries(comparisons));
  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <h3 className="text-sm font-semibold">{title}</h3>
        {entries.some(([, item]) => item.status === "regressed") ? <span className="font-mono text-[10px] uppercase tracking-wider text-error">Regression first</span> : null}
      </div>
      {entries.length ? (
        <div className="grid gap-3 sm:grid-cols-2">
          {entries.map(([key, item]) => <ComparisonMetricRow key={key} label={formatFailureType(key)} comparison={item} formatter={(value) => `${Math.round(value * 100)}%`} />)}
        </div>
      ) : (
        <p className="rounded-md border border-border bg-bg-card p-4 text-sm text-fg-muted">{emptyMessage}</p>
      )}
    </div>
  );
}

function stepToolName(step: StepComparison) {
  const toolCall = (step.after ?? step.before)?.tool_call;
  if (!toolCall || typeof toolCall !== "object") return null;
  const name = (toolCall as { name?: unknown }).name;
  return typeof name === "string" ? name : null;
}

function StepDiffRow({ step }: { step: StepComparison }) {
  const toolName = stepToolName(step);
  return (
    <li className={`rounded-md border p-4 ${statusClasses(step.status)}`}>
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="flex flex-wrap items-center gap-2">
          <span className="font-mono text-xs font-semibold">Step {step.index + 1}</span>
          <span className="rounded-full border border-current/20 px-2 py-0.5 text-[10px] uppercase tracking-wide">{step.kind}</span>
          {toolName ? <code className="font-mono text-[11px] text-fg-muted">{toolName}</code> : null}
        </div>
        <span className="text-[10px] font-semibold uppercase tracking-wide">{statusLabel(step.status)}</span>
      </div>
      {step.kind === "changed" ? (
        <div className="mt-3 grid gap-2 text-xs text-fg-muted sm:grid-cols-2">
          <p><span className="font-mono text-[10px] uppercase text-fg-subtle">Before</span><br />{String(step.before?.decision_summary ?? step.before?.observation ?? "No summary")}</p>
          <p><span className="font-mono text-[10px] uppercase text-fg-subtle">After</span><br />{String(step.after?.decision_summary ?? step.after?.observation ?? "No summary")}</p>
        </div>
      ) : null}
    </li>
  );
}

function StepComparisonList({ steps }: { steps: StepComparison[] }) {
  const ordered = [...steps].sort((left, right) => STATUS_RANK[left.status] - STATUS_RANK[right.status] || left.index - right.index);
  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <h3 id="step-diff-heading" className="text-sm font-semibold">Step diff</h3>
        {ordered.some((step) => step.status === "regressed") ? <span className="font-mono text-[10px] uppercase tracking-wider text-error">Regressions first</span> : null}
      </div>
      {ordered.length ? <ol className="space-y-2">{ordered.map((step) => <StepDiffRow key={step.index} step={step} />)}</ol> : <p className="rounded-md border border-border bg-bg-card p-4 text-sm text-fg-muted">No persisted step events are available for comparison yet.</p>}
    </div>
  );
}

function runStepCount(run: Run, events: EventEnvelope[]) {
  return run.metrics.step_count ?? run.metrics.steps ?? events.filter((event) => event.type === "step_completed").length;
}

export default function RunDetailPage() {
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();
  const [searchParams, setSearchParams] = useSearchParams();
  const requestedTab = searchParams.get("view");
  const activeTab: Tab = isTab(requestedTab) ? requestedTab : "trace";
  const [actionError, setActionError] = useState<string | null>(null);

  const { data: run, error: runError, isLoading, refetch: refetchRun } = useGetRunQuery(id ?? "", { skip: !id });
  const { events, connection, connectionError } = useRunStream(id);
  const status = run ? effectiveStatus(run, events) : null;
  const terminal = status ? !PENDING_STATUSES.includes(status) : false;
  const { data: experiment, refetch: refetchExperiment } = useGetExperimentQuery(run?.experiment_id ?? "", { skip: !run?.experiment_id });
  const { data: analysis, error: analysisError, isFetching: analysisLoading, refetch: refetchAnalysis } = useGetRunAnalysisQuery(id ?? "", { skip: !id || !terminal });
  const [cancelRun, { isLoading: isCancelling }] = useCancelRunMutation();
  const [replayPolicy, { isLoading: isReplaying }] = useReplayPolicyMutation();
  const [activatePolicy, { isLoading: isActivating }] = useActivatePolicyMutation();
  const [rejectPolicy, { isLoading: isRejecting }] = useRejectPolicyMutation();

  const lastEvent = events[events.length - 1];
  useEffect(() => {
    if (!lastEvent || !["run_completed", "run_failed", "run_cancelled"].includes(lastEvent.type)) return;
    void refetchRun();
    void refetchAnalysis();
    void refetchExperiment();
  }, [lastEvent, refetchAnalysis, refetchExperiment, refetchRun]);

  const policy = useMemo(() => {
    if (!run || !experiment) return null;
    const candidates = [experiment.candidate_policy, experiment.active_policy].filter(
      (item): item is Policy => Boolean(item),
    );
    return candidates.find((item) => item.source_run_id === (run.source_run_id ?? run.id)) ?? null;
  }, [experiment, run]);

  const comparison = useMemo(() => {
    if (!policy || !experiment) return null;
    const baseline = experiment.runs.find((item) => item.id === policy.source_run_id);
    const replay = policy.replay_run_id
      ? experiment.runs.find((item) => item.id === policy.replay_run_id)
      : undefined;
    return baseline && replay ? { baseline, replay } : null;
  }, [experiment, policy]);

  const baselineStream = useRunStream(comparison?.baseline.id);
  const replayStream = useRunStream(comparison?.replay.id);
  const baselineEvents = comparison?.baseline.id === id ? events : baselineStream.events;
  const replayEvents = comparison?.replay.id === id ? events : replayStream.events;
  const comparisonReady = Boolean(
    comparison &&
      !PENDING_STATUSES.includes(comparison.baseline.status) &&
      !PENDING_STATUSES.includes(comparison.replay.status),
  );
  const {
    data: baselineAnalysis,
    error: baselineAnalysisError,
    isFetching: baselineAnalysisLoading,
  } = useGetRunAnalysisQuery(comparison?.baseline.id ?? "", { skip: !comparisonReady });
  const {
    data: replayAnalysis,
    error: replayAnalysisError,
    isFetching: replayAnalysisLoading,
  } = useGetRunAnalysisQuery(comparison?.replay.id ?? "", { skip: !comparisonReady });
  const analysisComparison = useMemo(
    () => compareAnalysis(baselineAnalysis, replayAnalysis),
    [baselineAnalysis, replayAnalysis],
  );
  const stepComparison = useMemo(
    () => (comparison ? compareSteps(baselineEvents, replayEvents) : []),
    [baselineEvents, comparison, replayEvents],
  );
  const directionFor = (key: string, fallback: "maximize" | "minimize") =>
    baselineAnalysis?.metric_directions[key] ?? replayAnalysis?.metric_directions[key] ?? fallback;
  const runComparisons = comparison
    ? {
        score: compareMetric(comparison.baseline.score, comparison.replay.score, directionFor("score", "maximize")),
        steps: compareMetric(runStepCount(comparison.baseline, baselineEvents), runStepCount(comparison.replay, replayEvents), "minimize"),
        latency: compareMetric(comparison.baseline.metrics.latency_ms, comparison.replay.metrics.latency_ms, directionFor("latency_ms", "minimize")),
        tokens: compareMetric(totalTokens(comparison.baseline), totalTokens(comparison.replay), directionFor("total_tokens", "minimize")),
      }
    : null;
  const comparisonAnalysisLoading = baselineAnalysisLoading || replayAnalysisLoading;
  const comparisonAnalysisError = baselineAnalysisError || replayAnalysisError;

  const setTab = (tab: Tab) => setSearchParams({ view: tab });
  const tabKeyDown = (event: KeyboardEvent<HTMLButtonElement>, index: number) => {
    if (event.key !== "ArrowLeft" && event.key !== "ArrowRight") return;
    event.preventDefault();
    const delta = event.key === "ArrowRight" ? 1 : -1;
    const next = TABS[(index + delta + TABS.length) % TABS.length];
    setTab(next);
    requestAnimationFrame(() => document.getElementById(`run-tab-${next}`)?.focus());
  };

  const cancel = async () => {
    if (!run) return;
    setActionError(null);
    try {
      await cancelRun(run.id).unwrap();
      toast.info("Cancellation requested.");
      void refetchRun();
    } catch (error) {
      const message = getApiErrorMessage(error);
      setActionError(message);
      toast.error(message);
    }
  };

  const replay = async () => {
    if (!policy) return;
    setActionError(null);
    try {
      const updated = await replayPolicy(policy.id).unwrap();
      toast.success("Candidate replay started.");
      await refetchExperiment();
      if (updated.replay_run_id) navigate(`/runs/${updated.replay_run_id}?view=trace`);
    } catch (error) {
      const message = getApiErrorMessage(error);
      setActionError(message);
      toast.error(message);
    }
  };

  const activate = async () => {
    if (!policy) return;
    setActionError(null);
    try {
      await activatePolicy(policy.id).unwrap();
      toast.success("Policy activated for this experiment.");
      await refetchExperiment();
    } catch (error) {
      const message = getApiErrorMessage(error);
      setActionError(message);
      toast.error(message);
    }
  };

  const reject = async () => {
    if (!policy) return;
    setActionError(null);
    try {
      await rejectPolicy(policy.id).unwrap();
      toast.info("Candidate policy rejected.");
      await refetchExperiment();
    } catch (error) {
      const message = getApiErrorMessage(error);
      setActionError(message);
      toast.error(message);
    }
  };

  if (isLoading) {
    return <div className="mx-auto w-full max-w-6xl px-4 py-12 sm:px-6"><LoadingSkeleton variant="detail" /></div>;
  }

  if (runError || !run || !status) {
    return (
      <div className="mx-auto w-full max-w-3xl px-4 py-12 sm:px-6">
        <ErrorBanner title="Run unavailable" message={getApiErrorMessage(runError)} onRetry={() => void refetchRun()} />
        <Link to="/experiments" className="mt-6 inline-flex items-center gap-2 text-sm text-accent"><ArrowLeft className="h-4 w-4" /> Experiments</Link>
      </div>
    );
  }

  const canCancel = ["queued", "claimed", "running"].includes(status);

  return (
    <div className="mx-auto w-full max-w-6xl flex-1 px-4 py-8 sm:px-6 sm:py-12">
      <Link to={`/experiments/${run.experiment_id}`} className="mb-7 inline-flex items-center gap-2 rounded-md text-sm text-fg-muted hover:text-fg-primary">
        <ArrowLeft className="h-4 w-4" /> Experiment
      </Link>

      <div className="flex flex-col gap-5 border-b border-border pb-7 lg:flex-row lg:items-end lg:justify-between">
        <div>
          <div className="flex flex-wrap items-center gap-2">
            <LifecycleBadge status={status} />
            <span className="font-mono text-[11px] uppercase tracking-wider text-fg-subtle">{run.kind} · {run.id}</span>
            {connection === "reconnecting" ? <span role="status" className="font-mono text-[11px] text-accent-amber">Reconnecting…</span> : null}
          </div>
          <h1 className="mt-4 max-w-3xl text-2xl font-semibold tracking-tight sm:text-3xl">{run.evaluation_spec.task}</h1>
        </div>
        {canCancel ? (
          <button type="button" onClick={() => void cancel()} disabled={isCancelling} className="inline-flex w-fit items-center gap-2 rounded-md border border-error/25 bg-error/[0.06] px-4 py-2.5 text-sm font-semibold text-error hover:bg-error/10 disabled:cursor-wait disabled:opacity-60">
            <Stop className="h-4 w-4" weight="fill" /> {isCancelling ? "Requesting…" : "Cancel run"}
          </button>
        ) : null}
      </div>

      {run.error ? <div className="mt-6"><ErrorBanner title={status === "timed_out" ? "Run timed out" : "Run did not complete"} message={run.error} /></div> : null}
      {actionError ? <div className="mt-6"><ErrorBanner title="Action failed" message={actionError} /></div> : null}

      <div className="mt-6 grid grid-cols-2 gap-3 lg:grid-cols-4">
        <Metric label="Score" value={formatScore(run.score)} tone={run.score != null && run.score >= 0.8 ? "success" : status === "failed" ? "error" : "default"} />
        <Metric
          label="Steps"
          value={String(runStepCount(run, events))}
        />
        <Metric label="Latency" value={formatDuration(run.metrics?.latency_ms as number | undefined)} />
        <Metric label="Tokens" value={totalTokens(run)?.toLocaleString() ?? "—"} />
      </div>

      <div className="mt-8 border-b border-border">
        <div role="tablist" aria-label="Run detail views" className="flex gap-1 overflow-x-auto">
          {TABS.map((tab, index) => (
            <button
              key={tab}
              id={`run-tab-${tab}`}
              type="button"
              role="tab"
              aria-selected={activeTab === tab}
              aria-controls={`run-panel-${tab}`}
              tabIndex={activeTab === tab ? 0 : -1}
              onClick={() => setTab(tab)}
              onKeyDown={(event) => tabKeyDown(event, index)}
              className={`relative min-w-24 rounded-t-md px-4 py-3 text-sm font-semibold capitalize transition-colors ${activeTab === tab ? "text-fg-primary" : "text-fg-muted hover:text-fg-primary"}`}
            >
              {tab}
              {activeTab === tab ? <span className="absolute inset-x-3 bottom-0 h-0.5 bg-accent" /> : null}
            </button>
          ))}
        </div>
      </div>

      <div className="py-7">
        {activeTab === "trace" ? (
          <section id="run-panel-trace" role="tabpanel" aria-labelledby="run-tab-trace">
            <TracePanel events={events} connection={connection} connectionError={connectionError} terminal={terminal} />
          </section>
        ) : null}

        {activeTab === "analysis" ? (
          <section id="run-panel-analysis" role="tabpanel" aria-labelledby="run-tab-analysis">
            {!terminal || analysisLoading ? <LoadingSkeleton variant="detail" /> : null}
            {terminal && analysisError ? <ErrorBanner title="Analysis unavailable" message={getApiErrorMessage(analysisError)} onRetry={() => void refetchAnalysis()} /> : null}
            {analysis ? (
              <div className="space-y-6">
                <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
                  {Object.entries(analysis.dimensions).map(([dimension, value]) => (
                    <div key={dimension} className="rounded-md border border-border bg-bg-card p-4 shadow-inner-glow">
                      <p className="font-mono text-[10px] uppercase tracking-wider text-fg-subtle">{dimension}</p>
                      <div className="mt-3 h-1.5 overflow-hidden rounded-full bg-white/[0.06]"><div className="h-full rounded-full bg-accent-amber" style={{ width: `${Math.min(Math.max(value, 0), 1) * 100}%` }} /></div>
                      <div className="mt-2 flex items-baseline justify-between gap-2">
                        <p className="font-mono text-sm">{Math.round(value * 100)}%</p>
                        {analysis.metric_directions[dimension] ? <span className="text-[10px] text-fg-subtle">{analysis.metric_directions[dimension] === "minimize" ? "lower is better" : "higher is better"}</span> : null}
                      </div>
                    </div>
                  ))}
                </div>
                {Object.keys(analysis.failure_types).length ? (
                  <div className="flex flex-wrap items-center gap-2 border-b border-border pb-5">
                    <span className="font-mono text-[10px] uppercase tracking-wider text-fg-subtle">Failure classifications</span>
                    {Object.entries(analysis.failure_types).map(([failureType, value]) => (
                      <span key={failureType} className="rounded-full border border-accent-amber/20 bg-accent-amber/[0.06] px-2.5 py-1 text-xs capitalize text-fg-muted">
                        {formatFailureType(failureType)} {Math.round(value * 100)}%
                      </span>
                    ))}
                  </div>
                ) : null}
                <div className="rounded-md border border-border bg-bg-card p-5 shadow-inner-glow">
                  <div className="flex flex-wrap items-center justify-between gap-3">
                    <div>
                      <p className="font-mono text-[10px] uppercase tracking-wider text-fg-subtle">Dominant failure</p>
                      <h2 className="mt-1 text-lg font-semibold capitalize">{analysis.dominant_type ?? "No material failure"}</h2>
                    </div>
                    <span className="font-mono text-xs text-fg-muted">Failure rate {Math.round(analysis.failure_rate * 100)}%</span>
                  </div>
                  {analysis.evidence.length ? (
                    <ol className="mt-5 space-y-3">
                      {analysis.evidence.map((evidence, index) => (
                        <li key={`${evidence.dimension}-${evidence.step_index}-${index}`} className="flex gap-3 rounded-md border border-accent-amber/15 bg-accent-amber/[0.04] p-4">
                          <WarningCircle className="mt-0.5 h-5 w-5 shrink-0 text-accent-amber" weight="fill" />
                          <div>
                            <div className="flex flex-wrap items-center gap-2"><span className="text-sm font-semibold capitalize">{formatFailureType(evidence.failure_type ?? evidence.dimension)}</span>{evidence.step_index != null ? <span className="font-mono text-[10px] text-fg-subtle">Step {evidence.step_index + 1}</span> : null}</div>
                            <p className="mt-1 text-sm leading-6 text-fg-muted">{evidence.reason}</p>
                          </div>
                        </li>
                      ))}
                    </ol>
                  ) : (
                    <div className="mt-5 flex items-center gap-3 rounded-md border border-success/15 bg-success/[0.05] p-4 text-sm text-success"><CheckCircle className="h-5 w-5" weight="fill" /> No actionable failure evidence was found.</div>
                  )}
                </div>
              </div>
            ) : null}
          </section>
        ) : null}

        {activeTab === "improve" ? (
          <section id="run-panel-improve" role="tabpanel" aria-labelledby="run-tab-improve">
            {!policy ? (
              <EmptyState icon={Lightning} message="No policy candidate" description={terminal ? "This run did not produce an actionable candidate. Review the analysis evidence." : "The failure analyzer will create a candidate after this run reaches a terminal state."} />
            ) : (
              <div className="grid gap-6 lg:grid-cols-[minmax(0,1.15fr)_minmax(320px,0.85fr)]">
                <div>
                  <div className="mb-4 flex flex-wrap items-start justify-between gap-3">
                    <div><h2 className="text-lg font-semibold">Candidate policy</h2><p className="mt-1 max-w-2xl text-sm leading-6 text-fg-muted">{policy.rationale}</p></div>
                    <LifecycleBadge status={policy.status} />
                  </div>
                  <PolicyDiff policy={policy} baseline={comparison?.baseline ?? run} />
                </div>

                <aside className="space-y-4">
                  {comparison && runComparisons ? (
                    <div className="space-y-6">
                      <section className="space-y-4" aria-labelledby="replay-comparison-heading">
                        <div className="flex flex-wrap items-baseline justify-between gap-2">
                          <div>
                            <p className="font-mono text-[10px] uppercase tracking-wider text-success">Replay comparison</p>
                            <h2 id="replay-comparison-heading" className="mt-1 text-lg font-semibold">Persisted before / after facts</h2>
                          </div>
                          <span className="font-mono text-[10px] text-fg-subtle">{comparison.baseline.id.slice(0, 8)} → {comparison.replay.id.slice(0, 8)}</span>
                        </div>
                        <div className="grid gap-3 sm:grid-cols-2">
                          <ComparisonMetricRow label="Score delta" comparison={runComparisons.score} formatter={formatScore} />
                          <ComparisonMetricRow label="Steps" comparison={runComparisons.steps} formatter={(value) => String(value)} />
                          <ComparisonMetricRow label="Latency" comparison={runComparisons.latency} formatter={formatDuration} />
                          <ComparisonMetricRow label="Tokens" comparison={runComparisons.tokens} formatter={(value) => value.toLocaleString()} />
                        </div>
                      </section>

                      <section className="space-y-5" aria-labelledby="analysis-comparison-heading">
                        <div className="flex flex-wrap items-baseline justify-between gap-2">
                          <h2 id="analysis-comparison-heading" className="text-lg font-semibold">Analysis comparison</h2>
                          {comparisonAnalysisError ? <span className="text-xs text-error">Analysis unavailable</span> : null}
                        </div>
                        {comparisonAnalysisLoading ? <LoadingSkeleton variant="detail" /> : null}
                        {comparisonAnalysisError ? <ErrorBanner title="Comparison analysis unavailable" message={getApiErrorMessage(comparisonAnalysisError)} /> : null}
                        {analysisComparison ? (
                          <div className="space-y-5">
                            <ComparisonMetricRow label="Failure rate" comparison={analysisComparison.failureRate} formatter={(value) => `${Math.round(value * 100)}%`} />
                            <AnalysisComparisonList title="Dimensions" comparisons={analysisComparison.dimensions} emptyMessage="No persisted dimension metrics changed." />
                            <AnalysisComparisonList title="Failure classifications" comparisons={analysisComparison.failureTypes} emptyMessage="No persisted failure classifications were recorded." />
                          </div>
                        ) : null}
                      </section>

                      <section aria-labelledby="step-diff-heading">
                        <StepComparisonList steps={stepComparison} />
                      </section>

                      <Link to={`/runs/${comparison.replay.id}?view=trace`} className="inline-flex items-center gap-2 text-sm font-semibold text-success">Open replay trace <ArrowRight className="h-4 w-4" /></Link>
                    </div>
                  ) : (
                    <div className="rounded-md border border-border bg-bg-card p-5 shadow-inner-glow"><p className="text-sm font-semibold">Replay required</p><p className="mt-2 text-sm leading-6 text-fg-muted">Run the candidate against the same spec and seed before activation becomes available.</p></div>
                  )}

                  <div className="flex flex-col gap-2">
                    {policy.status === "candidate" ? <button type="button" onClick={() => void replay()} disabled={isReplaying} className="inline-flex items-center justify-center gap-2 rounded-md bg-accent px-4 py-2.5 text-sm font-semibold text-white hover:bg-accent/90 disabled:cursor-wait disabled:opacity-60"><Play className="h-4 w-4" weight="fill" />{isReplaying ? "Starting replay…" : "Run candidate replay"}</button> : null}
                    {policy.status === "validated" && (policy.score_delta ?? 0) > 0 ? <button type="button" onClick={() => void activate()} disabled={isActivating} className="inline-flex items-center justify-center gap-2 rounded-md bg-success px-4 py-2.5 text-sm font-semibold text-bg-root hover:bg-success/90 disabled:cursor-wait disabled:opacity-60"><CheckCircle className="h-4 w-4" weight="fill" />{isActivating ? "Activating…" : "Activate policy"}</button> : null}
                    {["candidate", "validated"].includes(policy.status) ? <button type="button" onClick={() => void reject()} disabled={isRejecting} className="rounded-md border border-border px-4 py-2.5 text-sm font-semibold text-fg-muted hover:bg-white/[0.04] hover:text-fg-primary disabled:cursor-wait disabled:opacity-60">{isRejecting ? "Rejecting…" : "Reject candidate"}</button> : null}
                    {policy.status === "active" ? <div className="flex items-center gap-2 rounded-md border border-success/20 bg-success/[0.05] p-4 text-sm text-success"><CheckCircle className="h-5 w-5" weight="fill" />Active for future runs in this experiment.</div> : null}
                    {policy.status === "rejected" ? <div className="rounded-md border border-border bg-bg-card p-4 text-sm text-fg-muted">This candidate was rejected. Run a new baseline to generate another candidate.</div> : null}
                  </div>
                </aside>
              </div>
            )}
          </section>
        ) : null}
      </div>
    </div>
  );
}
