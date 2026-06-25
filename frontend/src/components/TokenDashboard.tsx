import { motion } from "framer-motion";
import type { Step } from "../types";

interface TokenDashboardProps {
  totalTokens: number | null;
  contextWindowPeak: number | null;
  steps: Step[];
  contextWindowLimit?: number;
  inline?: boolean;
}

/** Per-tool token consumption accumulator. */
function computeToolBreakdown(
  steps: Step[],
): { name: string; prompt: number; completion: number; total: number }[] {
  const map = new Map<
    string,
    { name: string; prompt: number; completion: number }
  >();

  for (const s of steps) {
    if (!s.action) continue;
    const name = s.action.name;
    const entry = map.get(name) ?? { name, prompt: 0, completion: 0 };
    entry.prompt += s.token_prompt ?? 0;
    entry.completion += s.token_completion ?? 0;
    map.set(name, entry);
  }

  return [...map.values()]
    .map((e) => ({ ...e, total: e.prompt + e.completion }))
    .sort((a, b) => b.total - a.total);
}

export default function TokenDashboard({
  totalTokens,
  contextWindowPeak,
  steps,
  contextWindowLimit = 128000,
  inline = false,
}: TokenDashboardProps) {
  const avgTokens =
    totalTokens != null && steps.length > 0
      ? Math.round(totalTokens / steps.length)
      : null;

  const breakdown = computeToolBreakdown(steps);

  if (inline) {
    return (
      <div className="flex items-center gap-3 text-sm font-mono flex-wrap">
        <span className="inline-flex items-center gap-2 border border-white/[0.10] rounded-lg px-3 py-1.5 bg-white/[0.02] flex-shrink-0">
          <span className="text-fg-muted font-medium">Tokens</span>
          <span className="text-fg-primary font-bold tabular-nums text-base">
            {totalTokens != null ? (totalTokens / 1000).toFixed(1) + "k" : "--"}
          </span>
        </span>
        <span className="inline-flex items-center gap-2 border border-white/[0.10] rounded-lg px-3 py-1.5 bg-white/[0.02] flex-shrink-0">
          <span className="text-fg-muted font-medium">/Step</span>
          <span className="text-fg-primary font-bold tabular-nums text-base">
            {avgTokens != null ? avgTokens.toLocaleString() : "--"}
          </span>
        </span>
        <span className="inline-flex items-center gap-2 border border-white/[0.10] rounded-lg px-3 py-1.5 bg-white/[0.02] flex-shrink-0">
          <span className="text-fg-muted font-medium">Peak</span>
          <span className="text-fg-primary font-bold tabular-nums text-base">
            {contextWindowPeak != null
              ? `${Math.round((contextWindowPeak / contextWindowLimit) * 100)}%`
              : "--"}
          </span>
        </span>
        {/* Per-tool breakdown */}
        {breakdown.slice(0, 3).map((t) => (
          <span key={t.name} className="inline-flex items-center gap-2 border border-white/[0.10] rounded-lg px-3 py-1.5 bg-white/[0.02] flex-shrink-0">
            <span className="text-fg-muted font-medium">{t.name}</span>
            <span className="text-fg-primary font-bold tabular-nums text-base">{(t.total / 1000).toFixed(1)}k</span>
          </span>
        ))}
      </div>
    );
  }

  return (
    <motion.div
      initial={{ opacity: 0, y: -8 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.3, ease: "easeOut" }}
      className="grid grid-cols-4 gap-3"
    >
      {/* Total tokens */}
      <div className="bg-bg-card border border-border rounded-lg p-3">
        <div className="text-[10px] text-fg-muted font-sans uppercase tracking-wider mb-1">
          Total Tokens
        </div>
        <div className="text-xl font-mono tabular-nums text-fg-primary font-semibold">
          {totalTokens != null ? totalTokens.toLocaleString() : "--"}
        </div>
      </div>

      {/* Avg tokens per step */}
      <div className="bg-bg-card border border-border rounded-lg p-3">
        <div className="text-[10px] text-fg-muted font-sans uppercase tracking-wider mb-1">
          Avg / Step
        </div>
        <div className="text-xl font-mono tabular-nums text-fg-primary font-semibold">
          {avgTokens != null ? avgTokens.toLocaleString() : "--"}
        </div>
      </div>

      {/* Context window peak */}
      <div className="bg-bg-card border border-border rounded-lg p-3">
        <div className="text-[10px] text-fg-muted font-sans uppercase tracking-wider mb-1">
          Context Peak
        </div>
        <div className="text-xl font-mono tabular-nums text-fg-primary font-semibold">
          {contextWindowPeak != null
            ? `${Math.round((contextWindowPeak / contextWindowLimit) * 100)}%`
            : "--"}
          {contextWindowPeak != null && (
            <span className="text-xs text-fg-muted ml-1 font-normal">
              of {(contextWindowLimit / 1000).toFixed(0)}K
            </span>
          )}
        </div>
      </div>

      {/* Top tool by tokens */}
      <div className="bg-bg-card border border-border rounded-lg p-3">
        <div className="text-[10px] text-fg-muted font-sans uppercase tracking-wider mb-1">
          Top Tool
        </div>
        <div className="text-xl font-mono tabular-nums text-fg-primary font-semibold truncate">
          {breakdown.length > 0 ? (
            <>
              {breakdown[0].name}
              <span className="text-xs text-fg-muted ml-1 font-normal">
                {(breakdown[0].total / 1000).toFixed(1)}k
              </span>
            </>
          ) : (
            "--"
          )}
        </div>
      </div>

      {/* Breakdown list — spans full width */}
      {breakdown.length > 1 && (
        <div className="col-span-4 flex flex-wrap gap-1.5">
          {breakdown.slice(1).map((t) => (
            <span
              key={t.name}
              className="text-[10px] font-mono text-fg-muted bg-black/20 rounded px-1.5 py-0.5 tabular-nums"
            >
              {t.name}: {(t.total / 1000).toFixed(1)}k
            </span>
          ))}
        </div>
      )}
    </motion.div>
  );
}
