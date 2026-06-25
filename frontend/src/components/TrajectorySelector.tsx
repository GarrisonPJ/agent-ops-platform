import { motion } from "framer-motion";
import { CheckSquare, Square, GitCompare, Search, Loader2 } from "lucide-react";
import StatusBadge from "./StatusBadge";
import type { TrajectorySummary } from "../types";

interface TrajectorySelectorProps {
  trajectories: TrajectorySummary[];
  selectedIds: string[];
  onToggle: (id: string) => void;
  onCompare: () => void;
  onClear: () => void;
  loading: boolean;
  isLoading: boolean;
}

export default function TrajectorySelector({
  trajectories,
  selectedIds,
  onToggle,
  onCompare,
  onClear,
  loading,
  isLoading,
}: TrajectorySelectorProps) {
  const canCompare = selectedIds.length >= 2 && selectedIds.length <= 5;

  return (
    <div className="bg-bg-card border border-border rounded-xl shadow-sm overflow-hidden">
      {/* Header */}
      <div className="px-5 py-4 border-b border-border flex items-center justify-between">
        <div className="flex items-center gap-2">
          <GitCompare className="w-5 h-5 text-accent" />
          <h2 className="text-sm font-semibold font-mono text-fg-primary">
            Select Trajectories to Compare
          </h2>
        </div>
        <div className="flex items-center gap-2">
          {selectedIds.length > 0 && (
            <button
              onClick={onClear}
              className="text-xs text-fg-muted hover:text-fg-primary font-mono transition-colors px-2 py-1"
            >
              Clear ({selectedIds.length})
            </button>
          )}
          <button
            onClick={onCompare}
            disabled={!canCompare || loading}
            className="flex items-center gap-1.5 bg-accent text-bg-root text-sm font-semibold font-mono rounded-lg px-4 py-1.5 hover:brightness-110 active:scale-[0.97] disabled:opacity-50 disabled:cursor-not-allowed transition-all"
          >
            {loading ? (
              <Loader2 className="w-4 h-4 animate-spin" />
            ) : (
              <GitCompare className="w-4 h-4" />
            )}
            Compare
          </button>
        </div>
      </div>

      {/* Selection info */}
      <div className="px-5 py-2 border-b border-border bg-accent/5">
        <p className="text-xs text-fg-muted font-mono">
          {selectedIds.length === 0
            ? "Select 2-5 trajectories to compare"
            : `${selectedIds.length} selected (select ${Math.max(0, 2 - selectedIds.length)} more to compare)`}
        </p>
      </div>

      {/* List */}
      {isLoading ? (
        <div className="p-5 space-y-3">
          {Array.from({ length: 3 }).map((_, i) => (
            <div key={i} className="skeleton h-12 rounded-lg" />
          ))}
        </div>
      ) : trajectories.length === 0 ? (
        <div className="p-8 text-center">
          <Search className="w-10 h-10 text-fg-muted/30 mx-auto mb-3" />
          <p className="text-sm text-fg-muted font-mono">No trajectories found</p>
        </div>
      ) : (
        <div className="divide-y divide-border max-h-[400px] overflow-y-auto">
          {trajectories.map((traj) => {
            const isSelected = selectedIds.includes(traj.id);
            return (
              <motion.button
                key={traj.id}
                onClick={() => onToggle(traj.id)}
                className="w-full flex items-center gap-3 px-5 py-3 text-left hover:bg-bg-hover transition-colors"
              >
                <span className="flex-shrink-0">
                  {isSelected ? (
                    <CheckSquare className="w-5 h-5 text-accent" />
                  ) : (
                    <Square className="w-5 h-5 text-fg-muted/50" />
                  )}
                </span>
                <div className="flex-1 min-w-0">
                  <p className="text-sm font-mono text-fg-primary truncate">
                    {traj.task}
                  </p>
                  <p className="text-xs text-fg-muted font-mono mt-0.5">
                    {traj.step_count} steps &middot;{" "}
                    {new Date(traj.created_at).toLocaleString()}
                  </p>
                </div>
                <StatusBadge status={traj.status} variant="compact" />
              </motion.button>
            );
          })}
        </div>
      )}

      {/* Footer */}
      <div className="px-5 py-2.5 border-t border-border">
        <p className="text-xs text-fg-muted/60 font-mono">
          Trajectories appear side by side, aligned by step index.
          {selectedIds.length >= 2 && " Press Enter to compare, Esc to clear."}
        </p>
      </div>
    </div>
  );
}