import { useState, useEffect, useCallback } from "react";
import { motion, AnimatePresence } from "framer-motion";
import { ArrowLeft } from "lucide-react";
import { useNavigate, useSearchParams } from "react-router-dom";
import TrajectorySelector from "../components/TrajectorySelector";
import CompareTimeline from "../components/CompareTimeline";
import ErrorBanner from "../components/ErrorBanner";
import { useGetTracesQuery, useCompareTrajectoriesMutation } from "../services/api";
import type { CompareResponse } from "../types";

export default function ComparePage() {
  const navigate = useNavigate();
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
      const msg =
        err instanceof Error
          ? err.message
          : typeof err === "object" && err !== null && "data" in err
            ? JSON.stringify((err as { data: unknown }).data)
            : "Comparison failed";
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
    <>
      {/* Page header */}
      <header className="border-b border-border px-6 py-4">
        <div className="max-w-6xl mx-auto flex items-center gap-4">
          <button
            onClick={() => navigate("/traces")}
            className="flex items-center gap-1 text-fg-muted hover:text-fg-primary transition-colors"
          >
            <ArrowLeft className="w-4 h-4" />
            <span className="text-sm font-mono">Back</span>
          </button>
          <span className="text-sm font-mono text-fg-muted hidden sm:inline">
            /
          </span>
          <h1 className="text-sm font-mono font-semibold text-fg-primary">
            Compare Traces
          </h1>
        </div>
      </header>

      <main className="w-full px-6 py-8 space-y-6">
        {/* Trajectory selector (shown when no comparison results) */}
        {!compareData && (
          <motion.div
            initial={{ opacity: 0, y: 12 }}
            animate={{ opacity: 1, y: 0 }}
            className="max-w-6xl mx-auto"
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
          </motion.div>
        )}

        {/* Error state */}
        <AnimatePresence>
          {error && (
            <motion.div
              initial={{ opacity: 0, y: -8 }}
              animate={{ opacity: 1, y: 0 }}
              exit={{ opacity: 0, y: -8 }}
              className="max-w-6xl mx-auto"
            >
              <ErrorBanner title="Comparison Error" message={error} />
            </motion.div>
          )}
        </AnimatePresence>

        {/* Comparison view */}
        <AnimatePresence mode="wait">
          {compareData && (
            <motion.div
              key="comparison"
              initial={{ opacity: 0, y: 16 }}
              animate={{ opacity: 1, y: 0 }}
              exit={{ opacity: 0, y: -16 }}
              transition={{ duration: 0.25, ease: "easeOut" }}
              className="space-y-4"
            >
              {/* Back to selection button */}
              <button
                onClick={() => {
                  setCompareData(null);
                }}
                className="flex items-center gap-1.5 text-xs text-fg-muted hover:text-accent font-mono transition-colors mb-2"
              >
                <ArrowLeft className="w-3.5 h-3.5" />
                Back to selection
              </button>

              <CompareTimeline
                data={compareData}
                focusedTrajectory={focusedTrajectory}
                onFocusChange={setFocusedTrajectory}
              />
            </motion.div>
          )}
        </AnimatePresence>
      </main>
    </>
  );
}
