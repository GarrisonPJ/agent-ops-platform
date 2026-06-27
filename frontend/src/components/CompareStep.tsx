import { useState } from "react";
import { motion } from "framer-motion";
import { Clock, Wrench, Brain } from "lucide-react";
import { TRAJECTORY_COLORS } from "../lib/colors";
import type { CompareStepData } from "../types";

interface CompareStepProps {
  step: CompareStepData | null;
  trajectoryIndex: number;
  stepIndex: number;
  toolsDiffer: boolean;
}

export default function CompareStep({
  step,
  trajectoryIndex,
  stepIndex,
  toolsDiffer,
}: CompareStepProps) {
  if (!step) {
    return (
      <div className="h-full flex items-center justify-center border border-dashed border-border/50 rounded-lg py-8">
        <span className="text-xs text-fg-muted/40 font-mono">-- no step --</span>
      </div>
    );
  }

  const [expanded, setExpanded] = useState(false);

  const borderColor = toolsDiffer
    ? "border-accent-amber"
    : "border-transparent";
  const borderWidth = toolsDiffer ? "border-2" : "border";

  return (
    <motion.div
      initial={{ opacity: 0, y: 12 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{
        duration: 0.2,
        delay: stepIndex * 0.03 + trajectoryIndex * 0.02,
        ease: "easeOut",
      }}
      onClick={() => setExpanded((v) => !v)}
      className={`bg-bg-card ${borderWidth} ${borderColor} rounded-lg overflow-hidden cursor-pointer transition-shadow duration-200 ${
        expanded ? "relative z-10 ring-1 ring-accent/30 shadow-lg" : ""
      }`}
    >
      {/* Thought */}
      <div className="border-l-[3px] border-info p-3">
        <div className="flex items-center gap-1.5 mb-1.5">
          <Brain className="w-3 h-3 text-info" />
          <span className="text-[10px] text-fg-muted font-mono font-semibold uppercase tracking-wider">
            Thought
          </span>
        </div>
        <p className={`text-xs text-fg-primary whitespace-pre-wrap leading-relaxed ${expanded ? "line-clamp-none" : "line-clamp-3"}`}>
          {step.thought}
        </p>
      </div>

      {/* Tool Call */}
      {step.action && (
        <>
          <div className="border-t border-border" />
          <div className="px-3 py-2">
            <div className="flex items-center gap-1.5 mb-1.5">
              <Wrench className="w-3 h-3 text-fg-muted" />
              <span className="text-[10px] text-fg-muted font-mono font-semibold uppercase tracking-wider">
                Tool
              </span>
              <ToolBadge
                name={step.action.name}
                color={TRAJECTORY_COLORS[trajectoryIndex % TRAJECTORY_COLORS.length]}
              />
              {toolsDiffer && (
                <span className="ml-auto text-[10px] text-accent-amber font-mono font-semibold">
                  diff
                </span>
              )}
            </div>
            <pre className={`bg-black/30 text-fg-muted font-mono text-[10px] rounded p-2 overflow-x-auto ${expanded ? "" : "max-h-20 overflow-y-auto"}`}>
              {expanded ? JSON.stringify(step.action.arguments, null, 2) : truncateArgs(step.action.arguments)}
            </pre>
          </div>
        </>
      )}

      {/* Observation */}
      {step.observation && (
        <>
          <div className="border-t border-border" />
          <div className="border-l-[3px] border-accent p-3">
            <div className="text-[10px] text-fg-muted mb-1 font-mono font-semibold uppercase tracking-wider">
              Obs
            </div>
            <pre className={`text-xs text-fg-primary whitespace-pre-wrap font-mono leading-relaxed ${expanded ? "line-clamp-none" : "line-clamp-2"}`}>
              {step.observation}
            </pre>
          </div>
        </>
      )}

      {/* Latency */}
      <div className="border-t border-border px-3 py-1.5 flex items-center justify-end gap-1">
        <Clock className="w-2.5 h-2.5 text-fg-muted" />
        <span className="text-[10px] text-fg-muted font-mono tabular-nums">
          {(step.latency_ms / 1000).toFixed(1)}s
        </span>
      </div>
    </motion.div>
  );
}

function ToolBadge({ name, color }: { name: string; color: string }) {
  return (
    <span
      className="text-[10px] font-mono font-semibold px-1.5 py-0.5 rounded"
      style={{
        backgroundColor: `${color}18`,
        color,
      }}
    >
      {name}
    </span>
  );
}

function truncateArgs(args: Record<string, unknown>): string {
  const json = JSON.stringify(args, null, 1);
  if (json.length > 120) return json.slice(0, 120) + "…";
  return json;
}
