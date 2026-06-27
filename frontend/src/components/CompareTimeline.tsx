import { useMemo } from "react";
import { motion } from "framer-motion";
import { Clock, ArrowUpDown, ChevronLeft, ChevronRight, CheckCircle2 } from "lucide-react";
import CompareStep from "./CompareStep";
import StatusBadge from "./StatusBadge";
import { TRAJECTORY_COLORS } from "../lib/colors";
import type { CompareResponse } from "../types";

interface CompareTimelineProps {
  data: CompareResponse;
  focusedTrajectory: number;
  onFocusChange: (index: number) => void;
}

export default function CompareTimeline({
  data,
  focusedTrajectory,
  onFocusChange,
}: CompareTimelineProps) {
  const numTrajectories = data.trajectories.length;

  const diffCount = useMemo(
    () => data.aligned_steps.filter((s) => s.tools_differ).length,
    [data.aligned_steps],
  );

  const matchCount = useMemo(
    () =>
      data.aligned_steps.filter(
        (s) => s.tool_names.some(Boolean) && !s.tools_differ,
      ).length,
    [data.aligned_steps],
  );

  return (
    <div className="space-y-4">
      {/* Summary bar */}
      <motion.div
        initial={{ opacity: 0, y: -8 }}
        animate={{ opacity: 1, y: 0 }}
        className="flex flex-wrap items-center gap-4 px-4 py-3 bg-bg-card border border-border rounded-xl shadow-sm"
      >
        <span className="text-xs text-fg-muted font-mono">
          {data.max_steps} steps &middot; {numTrajectories} trajectories
        </span>
        <span className="text-xs text-accent-amber font-mono flex items-center gap-1">
          <ArrowUpDown className="w-3 h-3" />
          {diffCount} step{diffCount !== 1 ? "s" : ""} with tool differences
        </span>
        <span className="text-xs text-accent font-mono flex items-center gap-1">
          <CheckCircle2 className="w-3 h-3" />
          {matchCount} step{matchCount !== 1 ? "s" : ""} with matching tools
        </span>

        {/* Trajectory navigation */}
        <div className="ml-auto flex items-center gap-2">
          <button
            onClick={() => onFocusChange(Math.max(0, focusedTrajectory - 1))}
            disabled={focusedTrajectory === 0}
            aria-label="Previous trajectory"
            className="p-1 rounded text-fg-muted hover:text-fg-primary hover:bg-bg-hover disabled:opacity-30 disabled:cursor-not-allowed transition-colors transition-opacity"
            title="Previous trajectory"
          >
            <ChevronLeft className="w-4 h-4" />
          </button>
          <span className="text-xs text-fg-muted font-mono tabular-nums">
            {focusedTrajectory + 1} / {numTrajectories}
          </span>
          <button
            onClick={() =>
              onFocusChange(Math.min(numTrajectories - 1, focusedTrajectory + 1))
            }
            disabled={focusedTrajectory >= numTrajectories - 1}
            aria-label="Next trajectory"
            className="p-1 rounded text-fg-muted hover:text-fg-primary hover:bg-bg-hover disabled:opacity-30 disabled:cursor-not-allowed transition-colors transition-opacity"
            title="Next trajectory"
          >
            <ChevronRight className="w-4 h-4" />
          </button>
        </div>
      </motion.div>

      {/* Scrollable Container — trace-column layout */}
      <div className="overflow-auto pb-4" style={{ maxHeight: "calc(100vh - 220px)" }}>
        <div className="flex gap-4 min-w-0">
          {data.trajectories.map((meta, colIdx) => (
            <div key={meta.id} className="flex-1 flex flex-col gap-4 min-w-0">
              {/* Column header */}
              <motion.button
                initial={{ opacity: 0, y: -8 }}
                animate={{ opacity: 1, y: 0 }}
                transition={{ delay: colIdx * 0.05 }}
                className={`bg-bg-card border rounded-md shadow-inner-glow overflow-hidden transition-colors transition-shadow transition-opacity duration-300 cursor-pointer shrink-0 w-full text-left ${
                  colIdx === focusedTrajectory
                    ? "border-accent shadow-[inset_0_1px_0_rgba(255,255,255,0.2),0_0_0_1px_rgba(59,130,246,0.5)] bg-white/[0.02] opacity-100"
                    : "border-border hover:bg-white/[0.02] opacity-70"
                }`}
                onClick={() => onFocusChange(colIdx)}
              >
                <div
                  className="h-1"
                  style={{ backgroundColor: TRAJECTORY_COLORS[colIdx % TRAJECTORY_COLORS.length] }}
                />
                <div className="px-5 py-4">
                  <div className="flex items-center justify-between mb-2">
                    <p className="text-[13px] font-mono font-semibold text-fg-primary truncate leading-tight">
                      #{meta.id.substring(0, 4).toUpperCase()}
                    </p>
                    <StatusBadge status={meta.status} />
                  </div>
                  <p className="text-sm font-medium text-fg-primary truncate leading-relaxed mb-3">
                    {meta.task}
                  </p>
                  <div className="flex items-center gap-4 text-[11px] text-fg-muted font-mono">
                    <span className="tabular-nums">{meta.total_steps} steps</span>
                    <span className="flex items-center gap-1 tabular-nums">
                      <Clock className="w-3 h-3" strokeWidth={1.5} />
                      {(meta.total_latency_ms / 1000).toFixed(1)}s
                    </span>
                  </div>
                </div>
              </motion.button>

              {/* Steps for this trace — independent column */}
              {data.aligned_steps.map((aligned) => {
                const step = aligned.trajectories[colIdx];
                return (
                  <div
                    key={`${aligned.step_index}-${colIdx}`}
                    className={`transition-opacity duration-300 ${
                      colIdx === focusedTrajectory ? "opacity-100" : "opacity-70"
                    }`}
                  >
                    <div className="flex items-center gap-2 mb-1.5">
                      <span className="text-[10px] text-fg-muted/50 font-mono font-semibold tabular-nums">
                        #{aligned.step_index + 1}
                      </span>
                      {aligned.tools_differ && aligned.tool_names[colIdx] && (
                        <span className="text-[11px] bg-accent-amber/10 text-accent-amber font-mono font-semibold px-1.5 py-0.5 rounded inline-flex items-center gap-1">
                          <ArrowUpDown className="w-3 h-3" />
                          {aligned.tool_names[colIdx]}
                        </span>
                      )}
                    </div>
                    <CompareStep
                      step={step}
                      trajectoryIndex={colIdx}
                      stepIndex={aligned.step_index}
                      toolsDiffer={aligned.tools_differ}
                    />
                  </div>
                );
              })}
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}

