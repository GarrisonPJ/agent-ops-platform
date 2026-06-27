import { motion } from "framer-motion";
import { Clock, Shield, CheckCircle, XCircle } from "lucide-react";
import type { PolicyVersion } from "../types";
import { usePolicyActions } from "../hooks/usePolicyActions";
import LoadingSkeleton from "./LoadingSkeleton";
import ErrorBanner from "./ErrorBanner";
import EmptyState from "./EmptyState";

const CONFIDENCE_COLORS: Record<string, string> = {
  high: "text-emerald-400",
  medium: "text-amber-400",
  low: "text-zinc-400",
};

function formatDate(iso: string): string {
  const d = new Date(iso);
  return d.toLocaleDateString(undefined, {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

interface PolicyReviewQueueProps {
  policies: PolicyVersion[];
  isLoading: boolean;
  isError: boolean;
  error?: unknown;
  onRetry?: () => void;
}

export default function PolicyReviewQueue({
  policies,
  isLoading,
  isError,
  error,
  onRetry,
}: PolicyReviewQueueProps) {
  const { approvePolicy, rejectWithReason, isApproving, rejectingId } =
    usePolicyActions();

  // ── Loading state ─────────────────────────────────────────
  if (isLoading) {
    return (
      <motion.div
        initial={{ opacity: 0 }}
        animate={{ opacity: 1 }}
        className="bg-bg-card border border-border rounded-lg p-6 space-y-4"
      >
        <div className="h-3 bg-white/[0.05] rounded w-32" />
        <LoadingSkeleton variant="detail" />
        <LoadingSkeleton variant="detail" />
        <LoadingSkeleton variant="detail" />
      </motion.div>
    );
  }

  // ── Error state ───────────────────────────────────────────
  if (isError) {
    return (
      <motion.div
        initial={{ opacity: 0 }}
        animate={{ opacity: 1 }}
      >
        <ErrorBanner
          title="Failed to load review queue"
          message={
            error && "data" in error
              ? JSON.stringify((error as { data: unknown }).data)
              : "Could not fetch pending policies"
          }
          onRetry={onRetry}
        />
      </motion.div>
    );
  }

  // ── Empty state ───────────────────────────────────────────
  if (!policies || policies.length === 0) {
    return (
      <motion.div
        initial={{ opacity: 0, y: 16 }}
        animate={{ opacity: 1, y: 0 }}
      >
        <EmptyState
          icon={Shield}
          message="No policies pending review"
          description="All policies have been reviewed or none are waiting for approval"
        />
      </motion.div>
    );
  }

  // ── Render ────────────────────────────────────────────────
  return (
    <motion.div
      initial={{ opacity: 0, y: 12 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.4, ease: [0.16, 1, 0.3, 1] }}
      className="bg-bg-card border border-border rounded-lg p-6 space-y-4"
    >
      <div className="flex items-center justify-between">
        <h3 className="text-xs font-mono text-fg-muted uppercase tracking-wider">
          Review Queue ({policies.length})
        </h3>
        <span className="text-[10px] font-mono text-amber-400 bg-amber-400/10 px-2 py-0.5 rounded-full">
          {policies.length} pending
        </span>
      </div>

      <div className="space-y-3">
        {policies.map((policy) => {
          const maxRationale = 120;
          const truncated =
            policy.rationale.length > maxRationale
              ? policy.rationale.slice(0, maxRationale) + "…"
              : policy.rationale;

          return (
            <div
              key={policy.version_id}
              className="bg-bg-root border border-border rounded-lg p-4 space-y-3 hover:border-accent/20 transition-colors"
            >
              {/* Header */}
              <div className="flex items-center justify-between">
                <div className="flex items-center gap-2">
                  <span className="text-sm font-mono font-semibold text-fg-primary">
                    {policy.version_display}
                  </span>
                  <span
                    className={`text-[11px] font-mono ${CONFIDENCE_COLORS[policy.confidence]}`}
                  >
                    {policy.confidence.charAt(0).toUpperCase() +
                      policy.confidence.slice(1)}{" "}
                    Confidence
                  </span>
                </div>
                <span className="text-[10px] font-mono text-fg-subtle flex items-center gap-1">
                  <Clock className="w-3 h-3" />
                  {formatDate(policy.created_at)}
                </span>
              </div>

              {/* Rationale */}
              <p className="text-xs font-mono text-fg-muted leading-relaxed">
                {truncated}
              </p>

              {/* Actions */}
              <div className="flex items-center gap-2 pt-1">
                <button
                  onClick={() => approvePolicy(policy.version_id)}
                  disabled={isApproving}
                  className="flex items-center gap-1 px-3 py-1.5 rounded-lg text-[11px] font-mono font-semibold bg-emerald-500/10 text-emerald-400 border border-emerald-500/20 hover:bg-emerald-500/20 active:scale-[0.97] transition-colors transition-transform transition-opacity disabled:opacity-50 disabled:cursor-not-allowed"
                >
                  <CheckCircle className="w-3 h-3" />
                  Approve
                </button>
                <button
                  onClick={() => rejectWithReason(policy.version_id)}
                  disabled={rejectingId === policy.version_id}
                  className="flex items-center gap-1 px-3 py-1.5 rounded-lg text-[11px] font-mono font-semibold bg-red-500/10 text-red-400 border border-red-500/20 hover:bg-red-500/20 active:scale-[0.97] transition-colors transition-transform transition-opacity disabled:opacity-50 disabled:cursor-not-allowed"
                >
                  <XCircle className="w-3 h-3" />
                  Reject
                </button>
              </div>
            </div>
          );
        })}
      </div>
    </motion.div>
  );
}
