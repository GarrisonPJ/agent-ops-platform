import { Link } from "react-router-dom";
import LifecycleBadge from "./LifecycleBadge";
import { formatDate, formatScore } from "../lib/phase1Format";
import type { Run, TrialGroupSummary } from "../types/phase1";

function formatRate(value: number | null) {
  return value == null ? "—" : `${(value * 100).toFixed(1)}%`;
}

function publication(group: TrialGroupSummary) {
  if (group.state === "running") {
    return `${group.terminal}/${group.n} trials finished; pass^k is published when all finish.`;
  }
  if (group.state === "cancelled") {
    return "Not published: a trial was cancelled, so the group has no pass^k.";
  }
  return `${group.successes ?? 0}/${group.n} trials succeeded.`;
}

// Aggregates come from the Control Plane (ADR-0010); this view only displays them.
export default function TrialGroupsSection({
  groups,
  runs,
}: {
  groups: TrialGroupSummary[];
  runs: Run[];
}) {
  const runsById = new Map(runs.map((run) => [run.id, run]));
  return (
    <section aria-labelledby="trials-heading" className="mt-10">
      <div className="mb-4">
        <h2 id="trials-heading" className="text-lg font-semibold">
          Trial groups
        </h2>
        <p className="mt-1 text-sm text-fg-muted">
          Each group runs n trials on one seed. pass^k is the chance that all k trials succeed.
        </p>
      </div>
      <div className="grid gap-4">
        {groups.map((group) => (
          <article
            key={group.id}
            aria-label={`Trial group ${group.id}`}
            className="rounded-md border border-border bg-bg-card p-5 shadow-inner-glow"
          >
            <div className="flex flex-wrap items-start justify-between gap-4">
              <div>
                <p className="text-sm font-semibold">
                  {`${group.policy_id ? "Candidate policy replay" : "Baseline trials"} · n=${group.n}, k=${group.k}`}
                </p>
                <p className="mt-0.5 font-mono text-xs text-fg-muted">
                  seed {group.seed} · {formatDate(group.created_at)}
                </p>
              </div>
              <p className="text-sm text-fg-muted">{publication(group)}</p>
            </div>
            <dl className="mt-4 grid grid-cols-2 gap-3 font-mono text-sm sm:grid-cols-4">
              <div>
                <dt className="text-[10px] uppercase tracking-wider text-fg-subtle">pass^1</dt>
                <dd>{formatRate(group.pass_at_1)}</dd>
              </div>
              <div>
                <dt className="text-[10px] uppercase tracking-wider text-fg-subtle">
                  pass^{group.k}
                </dt>
                <dd>{formatRate(group.pass_at_k)}</dd>
              </div>
              <div>
                <dt className="text-[10px] uppercase tracking-wider text-fg-subtle">Mean score</dt>
                <dd>{formatScore(group.mean_score)}</dd>
              </div>
              <div>
                <dt className="text-[10px] uppercase tracking-wider text-fg-subtle">Mean steps</dt>
                <dd>{group.mean_steps == null ? "—" : group.mean_steps.toFixed(1)}</dd>
              </div>
            </dl>
            <ol className="mt-4 flex flex-wrap gap-2" aria-label="Trials">
              {group.run_ids.map((runId, index) => {
                const run = runsById.get(runId);
                return (
                  <li key={runId}>
                    <Link
                      to={`/runs/${runId}`}
                      className="inline-flex items-center gap-2 rounded-md border border-border px-2 py-1 text-xs hover:border-accent"
                    >
                      <span className="font-mono text-fg-subtle">#{index + 1}</span>
                      {run ? <LifecycleBadge status={run.status} /> : <span>—</span>}
                    </Link>
                  </li>
                );
              })}
            </ol>
          </article>
        ))}
      </div>
    </section>
  );
}
