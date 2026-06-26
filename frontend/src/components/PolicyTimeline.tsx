import { motion } from "framer-motion";
import { GitBranch } from "lucide-react";
import type { PolicyVersion } from "../types";
import LoadingSkeleton from "./LoadingSkeleton";
import EmptyState from "./EmptyState";

/* ------------------------------------------------------------------ */
/*  Status colour helpers                                              */
/* ------------------------------------------------------------------ */

const STATUS_STYLE: Record<string, { dot: string; line: string; label: string }> = {
  active: {
    dot: "bg-emerald-500 border-emerald-500 shadow-[0_0_8px_rgba(16,185,129,0.5)]",
    line: "bg-emerald-500/30",
    label: "text-emerald-400",
  },
  pending_review: {
    dot: "bg-amber-400 border-amber-400 shadow-[0_0_8px_rgba(251,191,36,0.5)]",
    line: "bg-amber-400/30",
    label: "text-amber-400",
  },
  reverted: {
    dot: "bg-red-500 border-red-500 shadow-[0_0_8px_rgba(239,68,68,0.5)]",
    line: "bg-red-500/30",
    label: "text-red-400",
  },
};

const STATUS_FALLBACK = {
  dot: "bg-zinc-500 border-zinc-500",
  line: "bg-zinc-500/30",
  label: "text-zinc-400",
};

function formatDate(iso: string): string {
  const d = new Date(iso);
  return d.toLocaleDateString(undefined, { month: "short", day: "numeric" });
}

/* ------------------------------------------------------------------ */
/*  Props                                                              */
/* ------------------------------------------------------------------ */

interface PolicyTimelineProps {
  policies: PolicyVersion[];
  selectedId: string | null;
  onSelect: (id: string) => void;
  loading: boolean;
}

/* ------------------------------------------------------------------ */
/*  Main component                                                     */
/* ------------------------------------------------------------------ */

export default function PolicyTimeline({
  policies,
  selectedId,
  onSelect,
  loading,
}: PolicyTimelineProps) {
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

  if (!policies || policies.length === 0) {
    return (
      <motion.div
        initial={{ opacity: 0, y: 16 }}
        animate={{ opacity: 1, y: 0 }}
      >
        <EmptyState
          icon={GitBranch}
          message="No policies yet"
          description="Policy versions will appear here once generated from failure analysis"
        />
      </motion.div>
    );
  }

  return (
    <motion.div
      initial={{ opacity: 0, y: 12 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.4, ease: [0.16, 1, 0.3, 1] }}
      className="bg-bg-card border border-border rounded-lg p-6"
    >
      <h3 className="text-xs font-mono text-fg-muted uppercase tracking-wider mb-6">
        Policy Timeline
      </h3>

      <div className="flex items-center gap-0 overflow-x-auto pb-2">
        {policies.map((policy, idx) => {
          const style = (STATUS_STYLE as Record<string, typeof STATUS_STYLE.active>)[policy.status] || STATUS_FALLBACK;
          const isSelected = policy.version_id === selectedId;
          const isLast = idx === policies.length - 1;

          return (
            <div key={policy.version_id} className="flex items-center gap-0 shrink-0">
              {/* Node */}
              <button
                onClick={() => onSelect(policy.version_id)}
                className="flex flex-col items-center gap-2 cursor-pointer group"
              >
                {/* Dot */}
                <div className="relative">
                  <div
                    className={`w-4 h-4 rounded-full border-2 transition-all duration-300 ${
                      style.dot
                    } ${
                      isSelected
                        ? "scale-150 shadow-glow-accent"
                        : "group-hover:scale-125"
                    }`}
                  />
                  {/* Selected glow ring */}
                  {isSelected && (
                    <motion.div
                      initial={{ opacity: 0, scale: 0.5 }}
                      animate={{ opacity: 1, scale: 1 }}
                      className="absolute -inset-2 rounded-full border border-accent/40"
                    />
                  )}
                </div>

                {/* Label */}
                <span
                  className={`text-xs font-mono font-semibold transition-colors ${
                    isSelected ? "text-accent" : "text-fg-muted group-hover:text-fg-primary"
                  }`}
                >
                  {policy.version_display}
                </span>

                {/* Date */}
                <span className="text-[10px] font-mono text-fg-subtle -mt-1">
                  {formatDate(policy.created_at)}
                </span>

                {/* Status label */}
                <span className={`text-[10px] font-mono uppercase ${style.label}`}>
                  {policy.status.replace("_", " ")}
                </span>
              </button>

              {/* Connector line */}
              {!isLast && (
                <div className="flex items-center mx-3">
                  <div className="w-12 h-[2px] rounded-full bg-white/[0.08]" />
                  <div
                    className="w-1.5 h-1.5 rotate-45 border-t border-r border-white/[0.15] -ml-[5px]"
                  />
                </div>
              )}
            </div>
          );
        })}
      </div>
    </motion.div>
  );
}
