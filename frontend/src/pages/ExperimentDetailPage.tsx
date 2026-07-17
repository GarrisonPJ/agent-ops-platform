import {
  ArrowLeft,
  ArrowRight,
  Flask,
  Play,
  ShieldCheck,
  Sparkle,
} from "@phosphor-icons/react";
import { Link, useNavigate, useParams } from "react-router-dom";
import EmptyState from "../components/EmptyState";
import ErrorBanner from "../components/ErrorBanner";
import LifecycleBadge from "../components/LifecycleBadge";
import LoadingSkeleton from "../components/LoadingSkeleton";
import { toast } from "../lib/toast";
import {
  formatDate,
  formatDuration,
  formatScore,
  getApiErrorMessage,
  totalTokens,
} from "../lib/phase1Format";
import {
  useCreateBaselineRunMutation,
  useGetExperimentQuery,
} from "../services/experimentsApi";

export default function ExperimentDetailPage() {
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();
  const { data: experiment, error, isLoading, refetch } = useGetExperimentQuery(id ?? "", {
    skip: !id,
  });
  const [createBaseline, { isLoading: isStarting }] = useCreateBaselineRunMutation();

  const startBaseline = async () => {
    if (!id) return;
    try {
      const run = await createBaseline(id).unwrap();
      navigate(`/runs/${run.id}?view=trace`);
    } catch (mutationError) {
      toast.error(getApiErrorMessage(mutationError));
    }
  };

  if (isLoading) {
    return (
      <div className="mx-auto w-full max-w-6xl px-4 py-12 sm:px-6">
        <LoadingSkeleton variant="detail" />
      </div>
    );
  }

  if (error || !experiment) {
    return (
      <div className="mx-auto w-full max-w-3xl px-4 py-12 sm:px-6">
        <ErrorBanner
          title="Experiment unavailable"
          message={getApiErrorMessage(error)}
          onRetry={() => void refetch()}
        />
        <Link to="/experiments" className="mt-6 inline-flex items-center gap-2 text-sm text-accent">
          <ArrowLeft className="h-4 w-4" /> Back to experiments
        </Link>
      </div>
    );
  }

  const runs = [...experiment.runs].sort(
    (a, b) => new Date(b.queued_at).getTime() - new Date(a.queued_at).getTime(),
  );

  return (
    <div className="mx-auto w-full max-w-6xl flex-1 px-4 py-8 sm:px-6 sm:py-12">
      <Link
        to="/experiments"
        className="mb-8 inline-flex items-center gap-2 rounded-md text-sm text-fg-muted transition-colors hover:text-fg-primary"
      >
        <ArrowLeft className="h-4 w-4" /> Experiments
      </Link>

      <div className="flex flex-col gap-6 border-b border-border pb-8 lg:flex-row lg:items-end lg:justify-between">
        <div className="max-w-3xl">
          <div className="flex flex-wrap items-center gap-2">
            <span className="font-mono text-xs uppercase tracking-[0.14em] text-fg-subtle">
              {experiment.scenario_id}
            </span>
            {experiment.active_policy ? <LifecycleBadge status="active" /> : null}
          </div>
          <h1 className="mt-3 text-3xl font-semibold tracking-tight sm:text-4xl">{experiment.name}</h1>
          <p className="mt-3 text-sm leading-6 text-fg-muted sm:text-base">{experiment.task}</p>
        </div>
        <button
          type="button"
          onClick={() => void startBaseline()}
          disabled={isStarting}
          className="inline-flex w-fit items-center gap-2 rounded-md bg-accent px-4 py-2.5 text-sm font-semibold text-white shadow-inner-glow-accent transition-colors hover:bg-accent/90 disabled:cursor-wait disabled:opacity-60"
        >
          <Play className="h-4 w-4" weight="fill" />
          {isStarting ? "Starting…" : runs.length ? "Run new baseline" : "Run baseline"}
        </button>
      </div>

      <section aria-labelledby="policy-heading" className="mt-8">
        <div className="mb-4 flex items-center justify-between">
          <div>
            <h2 id="policy-heading" className="text-lg font-semibold">Policy state</h2>
            <p className="mt-1 text-sm text-fg-muted">Human-reviewed changes for this experiment only.</p>
          </div>
        </div>

        <div className="grid gap-4 lg:grid-cols-2">
          {experiment.active_policy ? (
            <article className="rounded-md border border-success/20 bg-success/[0.05] p-5 shadow-inner-glow">
              <div className="flex items-start justify-between gap-4">
                <div className="flex items-center gap-3">
                  <ShieldCheck className="h-5 w-5 text-success" weight="fill" />
                  <div>
                    <p className="text-sm font-semibold">Active policy</p>
                    <p className="mt-0.5 font-mono text-xs text-fg-muted">{experiment.active_policy.id}</p>
                  </div>
                </div>
                <LifecycleBadge status={experiment.active_policy.status} />
              </div>
              <p className="mt-4 text-sm leading-6 text-fg-muted">{experiment.active_policy.rationale}</p>
            </article>
          ) : (
            <article className="rounded-md border border-border bg-bg-card p-5 shadow-inner-glow">
              <div className="flex items-center gap-3">
                <ShieldCheck className="h-5 w-5 text-fg-subtle" />
                <div>
                  <p className="text-sm font-semibold">No active policy</p>
                  <p className="mt-1 text-sm text-fg-muted">Validate a candidate replay before activation.</p>
                </div>
              </div>
            </article>
          )}

          {experiment.candidate_policy ? (
            <Link
              to={`/runs/${experiment.candidate_policy.source_run_id}?view=improve`}
              className="group rounded-md border border-accent-amber/20 bg-accent-amber/[0.05] p-5 shadow-inner-glow transition-colors hover:bg-accent-amber/[0.08]"
            >
              <div className="flex items-start justify-between gap-4">
                <div className="flex items-center gap-3">
                  <Sparkle className="h-5 w-5 text-accent-amber" weight="fill" />
                  <div>
                    <p className="text-sm font-semibold">Candidate improvement</p>
                    <p className="mt-0.5 text-xs text-fg-muted">Review evidence, replay, then decide.</p>
                  </div>
                </div>
                <LifecycleBadge status={experiment.candidate_policy.status} />
              </div>
              <div className="mt-4 flex items-center justify-between text-sm">
                <span className="text-fg-muted">
                  {experiment.candidate_policy.score_delta == null
                    ? "Awaiting replay"
                    : `${experiment.candidate_policy.score_delta > 0 ? "+" : ""}${experiment.candidate_policy.score_delta.toFixed(2)} score delta`}
                </span>
                <span className="inline-flex items-center gap-1 text-accent-amber">
                  Review <ArrowRight className="h-4 w-4 transition-transform group-hover:translate-x-1" />
                </span>
              </div>
            </Link>
          ) : (
            <article className="rounded-md border border-border bg-bg-card p-5 shadow-inner-glow">
              <div className="flex items-center gap-3">
                <Sparkle className="h-5 w-5 text-fg-subtle" />
                <div>
                  <p className="text-sm font-semibold">No candidate policy</p>
                  <p className="mt-1 text-sm text-fg-muted">A failed baseline with actionable evidence will create one.</p>
                </div>
              </div>
            </article>
          )}
        </div>
      </section>

      <section aria-labelledby="runs-heading" className="mt-10">
        <div className="mb-4 flex items-end justify-between gap-4">
          <div>
            <h2 id="runs-heading" className="text-lg font-semibold">Runs</h2>
            <p className="mt-1 text-sm text-fg-muted">Baseline and policy replays share the same evaluation spec.</p>
          </div>
          <span className="font-mono text-xs text-fg-subtle">{runs.length} total</span>
        </div>

        {runs.length === 0 ? (
          <EmptyState
            icon={Flask}
            message="No runs yet"
            description="Start the baseline to capture a trace and generate failure evidence."
            actionLabel="Run baseline"
            onAction={() => void startBaseline()}
          />
        ) : (
          <div className="overflow-x-auto rounded-md border border-border bg-bg-card shadow-inner-glow">
            <table className="w-full min-w-[760px] text-left">
              <thead className="border-b border-border bg-white/[0.02] font-mono text-[10px] uppercase tracking-wider text-fg-subtle">
                <tr>
                  <th className="px-5 py-3 font-semibold">Run</th>
                  <th className="px-5 py-3 font-semibold">Status</th>
                  <th className="px-5 py-3 font-semibold">Score</th>
                  <th className="px-5 py-3 font-semibold">Duration</th>
                  <th className="px-5 py-3 font-semibold">Tokens</th>
                  <th className="px-5 py-3 font-semibold">Started</th>
                  <th className="px-5 py-3"><span className="sr-only">Open</span></th>
                </tr>
              </thead>
              <tbody>
                {runs.map((run) => (
                  <tr key={run.id} className="border-b border-border last:border-0 hover:bg-white/[0.025]">
                    <td className="px-5 py-4">
                      <Link to={`/runs/${run.id}`} className="block rounded-sm">
                        <span className="font-semibold capitalize text-fg-primary hover:text-accent">{run.kind}</span>
                        <span className="mt-1 block font-mono text-[11px] text-fg-subtle">{run.id}</span>
                      </Link>
                    </td>
                    <td className="px-5 py-4"><LifecycleBadge status={run.status} /></td>
                    <td className="px-5 py-4 font-mono text-sm">{formatScore(run.score)}</td>
                    <td className="px-5 py-4 font-mono text-sm text-fg-muted">{formatDuration(run.metrics?.latency_ms as number | undefined)}</td>
                    <td className="px-5 py-4 font-mono text-sm text-fg-muted">{totalTokens(run)?.toLocaleString() ?? "—"}</td>
                    <td className="px-5 py-4 text-sm text-fg-muted">{formatDate(run.started_at ?? run.queued_at)}</td>
                    <td className="px-5 py-4 text-right">
                      <Link to={`/runs/${run.id}`} aria-label={`Open ${run.kind} run`} className="inline-flex rounded-md p-2 text-fg-subtle hover:bg-white/[0.04] hover:text-accent">
                        <ArrowRight className="h-4 w-4" />
                      </Link>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>
    </div>
  );
}
