import { useState, useEffect, useCallback } from "react";
import { motion, AnimatePresence } from "framer-motion";
import { useSearchParams } from "react-router-dom";
import TrajectorySelector from "../components/TrajectorySelector";
import CompareTimeline from "../components/CompareTimeline";
import ErrorBanner from "../components/ErrorBanner";
import { useGetTracesQuery, useCompareTrajectoriesMutation } from "../services/api";
import { formatMutationError } from "../lib/formatMutationError";
import type { CompareResponse } from "../types";

export default function ComparePage() {
  const [searchParams] = useSearchParams();
  const [selectedIds, setSelectedIds] = useState<string[]>([]);
  const [compareData, setCompareData] = useState<CompareResponse | null>(null);
  const [focusedTrajectory, setFocusedTrajectory] = useState(0);
  const [error, setError] = useState<string | null>(null);

  const [compareTrajectories, { isLoading }] = useCompareTrajectoriesMutation();
  const { data: tracesData, isLoading: tracesLoading } = useGetTracesQuery();

  // Pre-select trajectories from URL ?ids= query param
  useEffect(() => {
    const idsParam = searchParams.get("ids");
    if (idsParam) {
      const ids = idsParam.split(",").filter(Boolean).slice(0, 5);
      if (ids.length >= 2) {
        setSelectedIds(ids);
      }
    }
  }, [searchParams]);

  const handleToggle = useCallback((id: string) => {
    setSelectedIds((prev) => {
      if (prev.includes(id)) return prev.filter((x) => x !== id);
      if (prev.length >= 5) return prev;
      return [...prev, id];
    });
    setCompareData(null);
    setError(null);
  }, []);

  const handleCompare = useCallback(async () => {
    if (selectedIds.length < 2 || selectedIds.length > 5) return;
    setError(null);
    try {
      const result = await compareTrajectories({
        trajectory_ids: selectedIds,
      }).unwrap();
      setCompareData(result);
      setFocusedTrajectory(0);
    } catch (err: unknown) {
      const msg = formatMutationError(err);
      setError(msg);
    }
  }, [selectedIds, compareTrajectories]);

  const handleClear = useCallback(() => {
    setSelectedIds([]);
    setCompareData(null);
    setError(null);
  }, []);

  // Keyboard shortcuts
  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if (
        e.target instanceof HTMLInputElement ||
        e.target instanceof HTMLTextAreaElement
      ) {
        return;
      }

      if (e.key === "Escape") {
        if (compareData) {
          setCompareData(null);
        } else {
          handleClear();
        }
        e.preventDefault();
      }

      if (e.key === "Enter" && !compareData) {
        handleCompare();
        e.preventDefault();
      }

      if (compareData) {
        if (e.key === "ArrowLeft") {
          setFocusedTrajectory((prev) => Math.max(0, prev - 1));
          e.preventDefault();
        }
        if (e.key === "ArrowRight") {
          setFocusedTrajectory((prev) =>
            Math.min(compareData.trajectories.length - 1, prev + 1),
          );
          e.preventDefault();
        }
      }
    };

    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [compareData, handleCompare, handleClear]);

  return (
    <div className="flex-1 flex flex-col">
      {/* ── Title ──────────────────────────────────── */}
      <div className={`pt-8 pb-6 w-full shrink-0 flex items-center gap-4 ${compareData ? "px-8" : "max-w-5xl mx-auto px-6"}`}>
        {compareData && (
          <button
            onClick={() => setCompareData(null)}
            className="text-sm font-mono text-fg-muted hover:text-fg-primary transition-colors shrink-0 rounded-sm focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-accent"
          >
            ← Back to selection
          </button>
        )}
        <div>
          <h1 className="text-2xl font-semibold tracking-tight text-fg-primary">Compare</h1>
          <p className="text-sm text-fg-muted mt-1">Side-by-side trajectory comparison.</p>
        </div>
      </div>

      {!compareData ? (
        /* ── Selection ──────────────────────────── */
        <motion.div
          initial={{ opacity: 0, y: 12 }}
          animate={{ opacity: 1, y: 0 }}
          className="flex-1 overflow-auto max-w-5xl mx-auto px-6 pb-8 w-full"
        >
          <TrajectorySelector
            trajectories={tracesData?.trajectories ?? []}
            selectedIds={selectedIds}
            onToggle={handleToggle}
            onCompare={handleCompare}
            onClear={handleClear}
            loading={isLoading}
            isLoading={tracesLoading}
          />
          <AnimatePresence>
            {error && (
              <motion.div
                initial={{ opacity: 0, y: -8 }}
                animate={{ opacity: 1, y: 0 }}
                exit={{ opacity: 0, y: -8 }}
                className="mt-4"
              >
                <ErrorBanner title="Comparison Error" message={error} />
              </motion.div>
            )}
          </AnimatePresence>
        </motion.div>
      ) : (
        /* ── Comparison ──────────────────────────── */
        <motion.div
          key="comparison"
          initial={{ opacity: 0, y: 16 }}
          animate={{ opacity: 1, y: 0 }}
          className="flex-1 flex flex-col min-h-0"
        >
          <AnimatePresence>
            {error && (
              <motion.div
                initial={{ opacity: 0, y: -8 }}
                animate={{ opacity: 1, y: 0 }}
                exit={{ opacity: 0, y: -8 }}
                className="w-full px-8"
              >
                <ErrorBanner title="Comparison Error" message={error} />
              </motion.div>
            )}
          </AnimatePresence>
          <div className="flex-1 overflow-auto px-8 py-4">
            <CompareTimeline
              data={compareData}
              focusedTrajectory={focusedTrajectory}
              onFocusChange={setFocusedTrajectory}
            />
          </div>
        </motion.div>
      )}
    </div>
  );
}
