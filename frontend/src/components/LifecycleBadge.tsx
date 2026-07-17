import { CircleNotch } from "@phosphor-icons/react";
import type { PolicyStatus, RunStatus } from "../types/phase1";

type LifecycleStatus = RunStatus | PolicyStatus;

const LABELS: Record<LifecycleStatus, string> = {
  queued: "Queued",
  claimed: "Claimed",
  running: "Running",
  cancelling: "Cancelling",
  succeeded: "Succeeded",
  failed: "Failed",
  cancelled: "Cancelled",
  timed_out: "Timed out",
  candidate: "Candidate",
  replaying: "Replaying",
  validated: "Validated",
  rejected: "Rejected",
  active: "Active",
  superseded: "Superseded",
};

const STYLES: Record<LifecycleStatus, string> = {
  queued: "border-white/10 bg-white/[0.04] text-fg-muted",
  claimed: "border-info/20 bg-info/10 text-info",
  running: "border-accent/25 bg-accent/10 text-accent",
  cancelling: "border-accent-amber/25 bg-accent-amber/10 text-accent-amber",
  succeeded: "border-success/25 bg-success/10 text-success",
  failed: "border-error/25 bg-error/10 text-error",
  cancelled: "border-white/10 bg-white/[0.04] text-fg-muted",
  timed_out: "border-error/25 bg-error/10 text-error",
  candidate: "border-accent-amber/25 bg-accent-amber/10 text-accent-amber",
  replaying: "border-accent/25 bg-accent/10 text-accent",
  validated: "border-success/25 bg-success/10 text-success",
  rejected: "border-error/25 bg-error/10 text-error",
  active: "border-success/25 bg-success/10 text-success",
  superseded: "border-white/10 bg-white/[0.04] text-fg-muted",
};

const PENDING: LifecycleStatus[] = [
  "queued",
  "claimed",
  "running",
  "cancelling",
  "replaying",
];

export default function LifecycleBadge({ status }: { status: LifecycleStatus }) {
  return (
    <span
      className={`inline-flex items-center gap-1.5 rounded-full border px-2.5 py-1 font-mono text-[11px] font-semibold ${STYLES[status]}`}
    >
      {PENDING.includes(status) ? (
        <CircleNotch className="h-3 w-3 animate-spin" aria-hidden="true" />
      ) : (
        <span className="h-1.5 w-1.5 rounded-full bg-current" aria-hidden="true" />
      )}
      {LABELS[status]}
    </span>
  );
}
