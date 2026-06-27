import type { Step } from "../types";
import { Clock } from "lucide-react";
import { motion } from "framer-motion";
import { cn } from "../lib/utils";

interface StepCardProps {
  step: Step;
  isResult?: boolean;
  isSelected?: boolean;
  isPlaying?: boolean;
  onClick?: () => void;
  footer?: React.ReactNode;
}

export default function StepCard({
  step,
  isResult = false,
  isSelected = false,
  isPlaying = false,
  onClick,
  footer,
}: StepCardProps) {
  const isFinal = step.action === null || isResult;
  const isFailed =
    step.action !== null &&
    (step.observation?.toLowerCase().includes("error") ||
     step.observation?.toLowerCase().includes("failed") ||
     step.observation?.toLowerCase().includes("timeout") ||
     step.observation?.toLowerCase().includes("exception"));

  return (
    <motion.div
      initial={{ opacity: 0, x: -8 }}
      animate={{ opacity: 1, x: 0 }}
      transition={{ duration: 0.35, ease: [0.16, 1, 0.3, 1] }}
      className={cn(
        "relative pl-8 pb-4 border-l border-border last:border-l-transparent last:pb-0 group step-card-timeline",
        isPlaying && "flow-active",
        isSelected && isPlaying && "step-current"
      )}
    >
      {/* ── Timeline node ──────────────────────────────── */}
      <span
        className={cn(
          "absolute -left-[5px] top-4 w-[9px] h-[9px] rounded-full transition-colors duration-300 z-10",
          isFinal
            ? "bg-success shadow-[0_0_8px_rgb(34_197_94/0.4)]"
            : isFailed
              ? "bg-error shadow-[0_0_8px_rgb(239_68_68/0.4)]"
              : "bg-accent shadow-[0_0_8px_rgb(59_130_246/0.4)]"
        )}
      />

      {/* ── Double-Bezel: Outer Shell ────────────────── */}
      <div className={cn(
        "p-[1px] rounded-lg transition-colors transition-shadow duration-700 ease-spring",
        isSelected && isPlaying
          ? "bg-gradient-to-b from-accent/40 to-accent/15 shadow-[0_0_30px_rgb(59_130_246/0.30)]"
          : isSelected
            ? "bg-gradient-to-b from-accent/30 to-accent/10 shadow-[0_0_20px_rgb(59_130_246/0.15)]"
            : "bg-border/50 hover:bg-border"
      )}>
        {/* ── Inner Core ─────────────────────────────── */}
        <div
          onClick={onClick}
          className={cn(
            "rounded-lg p-4 transition-colors transition-shadow transition-transform duration-700 ease-spring cursor-pointer active:scale-[0.985]",
            isSelected
              ? "bg-white/[0.06] shadow-[inset_0_1px_0_rgb(255_255_255/0.06)]"
              : "bg-bg-card shadow-[inset_0_1px_0_rgb(255_255_255/0.03)] group-hover/card:bg-bg-card-raised group-hover/card:shadow-[inset_0_1px_0_rgb(255_255_255/0.05)]"
          )}
        >
          {/* Label row */}
          <div className="flex items-center gap-2 mb-2">
            <span className={cn(
              "text-[10px] uppercase tracking-[0.15em] font-semibold select-none px-2 py-0.5 rounded-full border",
              isFinal
                ? "text-success bg-success/8 border-success/15"
                : isFailed
                  ? "text-error bg-error/8 border-error/15"
                  : "text-fg-muted bg-white/[0.03] border-white/[0.05] group-hover/card:text-fg-primary group-hover/card:border-white/[0.1] transition-colors"
            )}>
              {isFinal ? "Result" : "Thought"}
            </span>
            {step.action && (
              <span className="text-[11px] font-mono font-medium text-accent bg-accent/8 border border-accent/15 rounded-md px-2 py-0.5">
                {step.action.name}
              </span>
            )}
          </div>

          {/* Thought text */}
          <p className="text-fg-primary text-[13px] leading-relaxed whitespace-pre-wrap mb-3">
            {step.thought}
          </p>

          {/* Footer */}
          <div className="flex items-center gap-2 text-fg-subtle">
            {footer ?? (
              <span className="flex items-center gap-1.5 text-[11px] font-mono">
                <Clock className="w-3 h-3" strokeWidth={1.5} />
                {(step.latency_ms / 1000).toFixed(1)}s
              </span>
            )}
          </div>
        </div>
      </div>
    </motion.div>
  );
}
