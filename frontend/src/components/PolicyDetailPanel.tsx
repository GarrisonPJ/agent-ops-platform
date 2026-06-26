import { useState } from "react";
import { motion } from "framer-motion";
import {
  FileText,
  ChevronDown,
  ChevronUp,
  TrendingUp,
  TrendingDown,
  Clock,
  Shield,
  Activity,
} from "lucide-react";
import type { PolicyVersion } from "../types";
import LoadingSkeleton from "./LoadingSkeleton";
import EmptyState from "./EmptyState";

/* ------------------------------------------------------------------ */
/*  Helpers                                                            */
/* ------------------------------------------------------------------ */

const STATUS_BADGE: Record<
  PolicyVersion["status"],
  { bg: string; text: string; label: string }
> = {
  active: {
    bg: "bg-emerald-500/10 border-emerald-500/20",
    text: "text-emerald-400",
    label: "Active",
  },
  pending_review: {
    bg: "bg-amber-400/10 border-amber-400/20",
    text: "text-amber-400",
    label: "Pending Review",
  },
  reverted: {
    bg: "bg-red-500/10 border-red-500/20",
    text: "text-red-400",
    label: "Reverted",
  },
  archived: {
    bg: "bg-zinc-500/10 border-zinc-500/20",
    text: "text-zinc-400",
    label: "Archived",
  },
};

const CONFIDENCE_BADGE: Record<
  PolicyVersion["confidence"],
  { bg: string; text: string }
> = {
  high: {
    bg: "bg-emerald-500/8",
    text: "text-emerald-400",
  },
  medium: {
    bg: "bg-amber-400/8",
    text: "text-amber-400",
  },
  low: {
    bg: "bg-zinc-500/8",
    text: "text-zinc-400",
  },
};

function formatDateTime(iso: string): string {
  const d = new Date(iso);
  return d.toLocaleString(undefined, {
    year: "numeric",
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

/* ------------------------------------------------------------------ */
/*  Props                                                              */
/* ------------------------------------------------------------------ */

interface PolicyDetailPanelProps {
  policy: PolicyVersion | null;
  loading: boolean;
  onApprove?: (versionId: string) => void;
  onReject?: (versionId: string) => void;
  approving?: boolean;
}

/* ------------------------------------------------------------------ */
/*  Patch row                                                          */
/* ------------------------------------------------------------------ */

function PatchRow({
  label,
  value,
  isCode,
}: {
  label: string;
  value: string | undefined | null;
  isCode?: boolean;
}) {
  const [expanded, setExpanded] = useState(false);
  const MAX_LENGTH = 120;
  const needsTruncation = value && value.length > MAX_LENGTH;

  if (!value) return null;

  return (
    <div className="space-y-1.5">
      <span className="text-[11px] font-mono text-fg-muted uppercase tracking-wider">
        {label}
      </span>
      <div
        className={`text-xs font-mono text-fg-primary bg-bg-root rounded-lg border border-border ${
          isCode ? "p-3 overflow-x-auto" : "px-3 py-2"
        }`}
      >
        {needsTruncation && !expanded ? (
          <div>
            <span>{value.slice(0, MAX_LENGTH)}...</span>
            <button
              onClick={() => setExpanded(true)}
              className="ml-1 inline-flex items-center gap-0.5 text-accent hover:underline"
            >
              Show all
              <ChevronDown className="w-3 h-3" />
            </button>
          </div>
        ) : (
          <div>
            <span className="whitespace-pre-wrap break-all">{value}</span>
            {needsTruncation && expanded && (
              <button
                onClick={() => setExpanded(false)}
                className="ml-1 inline-flex items-center gap-0.5 text-fg-muted hover:text-accent"
              >
                <ChevronUp className="w-3 h-3" />
              </button>
            )}
          </div>
        )}
      </div>
    </div>
  );
}

/* ------------------------------------------------------------------ */
/*  Main component                                                     */
/* ------------------------------------------------------------------ */

export default function PolicyDetailPanel({
  policy,
  loading,
  onApprove,
  onReject,
  approving,
}: PolicyDetailPanelProps) {
  if (loading) {
    return (
      <motion.div
        initial={{ opacity: 0 }}
        animate={{ opacity: 1 }}
        className="bg-bg-card border border-border rounded-lg p-6"
      >
        <LoadingSkeleton variant="detail" />
      </motion.div>
    );
  }

  if (!policy) {
    return (
      <motion.div
        initial={{ opacity: 0, y: 16 }}
        animate={{ opacity: 1, y: 0 }}
      >
        <EmptyState
          icon={FileText}
          message="Select a policy to view details"
          description="Click a version in the timeline above to inspect its patch and metadata"
        />
      </motion.div>
    );
  }

  const statusStyle = STATUS_BADGE[policy.status];
  const confidenceStyle = CONFIDENCE_BADGE[policy.confidence];
  const patch = policy.patch;

  return (
    <motion.div
      initial={{ opacity: 0, y: 12 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.4, ease: [0.16, 1, 0.3, 1] }}
      className="bg-bg-card border border-border rounded-lg p-6 space-y-6"
    >
      {/* Header */}
      <div className="flex items-start justify-between">
        <div>
          <h2 className="text-lg font-semibold font-mono text-fg-primary">
            {policy.version_display}
          </h2>
          <div className="flex items-center gap-2 mt-2">
            {/* Status badge */}
            <span
              className={`inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[11px] font-mono font-medium border ${statusStyle.bg} ${statusStyle.text}`}
            >
              <Shield className="w-3 h-3" />
              {statusStyle.label}
            </span>

            {/* Confidence badge */}
            <span
              className={`inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[11px] font-mono font-medium ${confidenceStyle.bg} ${confidenceStyle.text}`}
            >
              <Activity className="w-3 h-3" />
              {policy.confidence.charAt(0).toUpperCase() +
                policy.confidence.slice(1)}{" "}
              Confidence
            </span>
          </div>
        </div>

        {/* Score delta */}
        {policy.score_delta !== null && (
          <div
            className={`flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-sm font-mono font-semibold ${
              policy.score_delta >= 0
                ? "text-emerald-400 bg-emerald-500/10 border border-emerald-500/20"
                : "text-red-400 bg-red-500/10 border border-red-500/20"
            }`}
          >
            {policy.score_delta >= 0 ? (
              <TrendingUp className="w-4 h-4" />
            ) : (
              <TrendingDown className="w-4 h-4" />
            )}
            {policy.score_delta >= 0 ? "+" : ""}
            {policy.score_delta.toFixed(2)}
          </div>
        )}
      </div>

      {/* Date */}
      <div className="flex items-center gap-1.5 text-xs font-mono text-fg-subtle">
        <Clock className="w-3.5 h-3.5" />
        {formatDateTime(policy.created_at)}
      </div>

      {/* Rationale */}
      <div className="space-y-1.5">
        <span className="text-[11px] font-mono text-fg-muted uppercase tracking-wider">
          Rationale
        </span>
        <p className="text-sm text-fg-primary font-mono leading-relaxed bg-bg-root rounded-lg border border-border px-3 py-2">
          {policy.rationale}
        </p>
      </div>

      {/* Patch */}
      {patch && (
        <div className="space-y-3">
          <span className="text-[11px] font-mono text-fg-muted uppercase tracking-wider">
            Patch
          </span>
          <div className="space-y-3">
            <PatchRow
              label="System Prompt Suffix"
              value={patch.system_prompt_suffix}
              isCode
            />
            {patch.tool_priority_bias && (
              <PatchRow
                label="Tool Priority Bias"
                value={JSON.stringify(patch.tool_priority_bias, null, 2)}
                isCode
              />
            )}
            <PatchRow
              label="Context Strategy"
              value={patch.context_strategy}
            />
            {patch.max_steps_override !== undefined && (
              <PatchRow
                label="Max Steps Override"
                value={String(patch.max_steps_override)}
              />
            )}
          </div>
        </div>
      )}

      {/* Reject reason */}
      {policy.reject_reason && (
        <div className="space-y-1.5">
          <span className="text-[11px] font-mono text-fg-muted uppercase tracking-wider">
            Reject Reason
          </span>
          <p className="text-sm text-red-400 font-mono leading-relaxed bg-red-500/5 rounded-lg border border-red-500/20 px-3 py-2">
            {policy.reject_reason}
          </p>
        </div>
      )}

      {/* Approve / Reject buttons (only for pending_review) */}
      {policy.status === "pending_review" && (onApprove || onReject) && (
        <div className="flex items-center gap-3 pt-2 border-t border-border">
          {onApprove && (
            <button
              onClick={() => onApprove(policy.version_id)}
              disabled={approving}
              className="flex items-center gap-1.5 px-4 py-2 rounded-lg text-xs font-mono font-semibold bg-emerald-500/10 text-emerald-400 border border-emerald-500/20 hover:bg-emerald-500/20 active:scale-[0.97] transition-all disabled:opacity-50 disabled:cursor-not-allowed"
            >
              <TrendingUp className="w-3.5 h-3.5" />
              Approve
            </button>
          )}
          {onReject && (
            <button
              onClick={() => onReject(policy.version_id)}
              disabled={approving}
              className="flex items-center gap-1.5 px-4 py-2 rounded-lg text-xs font-mono font-semibold bg-red-500/10 text-red-400 border border-red-500/20 hover:bg-red-500/20 active:scale-[0.97] transition-all disabled:opacity-50 disabled:cursor-not-allowed"
            >
              <TrendingDown className="w-3.5 h-3.5" />
              Reject
            </button>
          )}
        </div>
      )}
    </motion.div>
  );
}
