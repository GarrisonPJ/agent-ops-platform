import { useState, useEffect } from "react";
import { motion } from "framer-motion";
import {
  Warning,
  Bug,
  CaretDown,
} from "@phosphor-icons/react";
import EmptyState from "../../components/EmptyState";
import FailureDistributionChart from "../../components/FailureDistributionChart";
import type {
  FailureReport,
  FailureEvidence,
  TrajectorySummary,
} from "../../types";
import {
  useGetTracesQuery,
  useAnalyzeTrajectoryMutation,
} from "../../services/api";
import { toast } from "../../lib/toast";

export default function FailuresTab() {
  // ── state ─────────────────────────────────────────────────────────
  const [selectedTrajectoryId, setSelectedTrajectoryId] = useState("");
  const [failureReport, setFailureReport] = useState<FailureReport | null>(null);
  const [failureLoading, setFailureLoading] = useState(false);
  const [failureError, setFailureError] = useState<string | null>(null);

  // ── API hooks ────────────────────────────────────────────────────
  const { data: tracesData } = useGetTracesQuery();
  const [analyzeTrajectory] = useAnalyzeTrajectoryMutation();

  // ── analysis handler ────────────────────────────────────────────
  const handleAnalyze = async (trajectoryId: string) => {
    if (!trajectoryId) return;
    setFailureLoading(true);
    setFailureError(null);
    setFailureReport(null);
    try {
      const res = await analyzeTrajectory({
        trajectory_id: trajectoryId,
      }).unwrap();
      setFailureReport(res);
    } catch (err: unknown) {
      const msg =
        err instanceof Error
          ? err.message
          : typeof err === "object" && err !== null && "data" in err
            ? JSON.stringify((err as { data: unknown }).data)
            : "Analysis failed";
      setFailureError(msg);
      toast.error(msg);
    } finally {
      setFailureLoading(false);
    }
  };

  // ── when selected trajectory changes, auto-analyze ──────────────
  useEffect(() => {
    if (!selectedTrajectoryId) return;
    handleAnalyze(selectedTrajectoryId);
  }, [selectedTrajectoryId]);

  return (
    <motion.div
      initial={{ opacity: 0, y: 12 }}
      animate={{ opacity: 1, y: 0 }}
      className="space-y-6"
    >
      {/* Trajectory selector */}
      <div className="bg-bg-card border border-border rounded-lg p-6 space-y-4">
        <div className="space-y-2">
          <label className="block text-xs font-mono text-fg-muted uppercase tracking-wider">
            Select Trajectory
          </label>
          <div className="relative">
            <select
              value={selectedTrajectoryId}
              onChange={(e) => setSelectedTrajectoryId(e.target.value)}
              className="w-full appearance-none bg-bg-root border border-border rounded-lg px-3 py-2 pr-8 text-sm text-fg-primary font-mono outline-none focus:border-accent/50"
            >
              <option value="">-- Select a trajectory --</option>
              {tracesData?.trajectories?.map((t: TrajectorySummary) => (
                <option key={t.id} value={t.id}>
                  {t.id.substring(0, 8)}… — {t.task.substring(0, 60)}{t.task.length > 60 ? "…" : ""} ({t.status})
                </option>
              ))}
            </select>
            <CaretDown className="absolute right-3 top-1/2 -translate-y-1/2 w-3.5 h-3.5 text-fg-muted pointer-events-none" />
          </div>
        </div>
      </div>

      {/* Analysis results */}
      {selectedTrajectoryId && (
        <>
          {/* Failure Distribution Chart */}
          <FailureDistributionChart
            data={
              failureReport
                ? {
                    planning: failureReport.dimensions.planning ?? 0,
                    execution: failureReport.dimensions.execution ?? 0,
                    context: failureReport.dimensions.context ?? 0,
                    budget: failureReport.dimensions.budget ?? 0,
                  }
                : null
            }
            loading={failureLoading}
            error={failureError}
          />

          {/* Dominant failure type */}
          {failureReport && failureReport.dominant && (
            <motion.div
              initial={{ opacity: 0, y: 8 }}
              animate={{ opacity: 1, y: 0 }}
              className="bg-bg-card border border-border rounded-lg p-5 flex items-center gap-4"
            >
              <span className="flex items-center justify-center w-10 h-10 rounded-xl bg-accent-amber/10 ring-1 ring-accent-amber/20 flex-shrink-0">
                <Warning className="w-5 h-5 text-accent-amber" />
              </span>
              <div>
                <p className="text-xs font-mono text-fg-muted uppercase tracking-wider">
                  Dominant Failure Type
                </p>
                <p className="text-sm font-mono text-accent-amber font-semibold mt-0.5 capitalize">
                  {failureReport.dominant}
                </p>
              </div>
            </motion.div>
          )}

          {/* Evidence list */}
          {failureReport && failureReport.evidence.length > 0 && (
            <motion.div
              initial={{ opacity: 0, y: 8 }}
              animate={{ opacity: 1, y: 0 }}
              className="bg-bg-card border border-border rounded-lg p-6 space-y-4"
            >
              <h3 className="text-xs font-mono text-fg-muted uppercase tracking-wider">
                Failure Evidence ({failureReport.evidence.length})
              </h3>
              <div className="space-y-3">
                {failureReport.evidence.map((ev: FailureEvidence, i: number) => (
                  <div
                    key={i}
                    className="bg-bg-root border border-border rounded-lg p-4 space-y-2"
                  >
                    <div className="flex items-center justify-between">
                      <div className="flex items-center gap-2">
                        <span
                          className="w-2 h-2 rounded-full"
                          style={{
                            backgroundColor:
                              ev.dimension === "planning"
                                ? "#4b8cf7"
                                : ev.dimension === "execution"
                                  ? "#ef4444"
                                  : ev.dimension === "context"
                                    ? "#f5a623"
                                    : "#2dd4bf",
                          }}
                        />
                        <span className="text-xs font-mono text-fg-muted uppercase">
                          {ev.dimension}
                        </span>
                        <span className="text-xs font-mono text-fg-subtle">
                          Step {ev.step_index}
                        </span>
                      </div>
                      <span
                        className="text-xs font-mono tabular-nums"
                        style={{
                          color:
                            ev.severity >= 0.7
                              ? "#ef4444"
                              : ev.severity >= 0.4
                                ? "#f5a623"
                                : "#878d96",
                        }}
                      >
                        severity: {ev.severity.toFixed(2)}
                      </span>
                    </div>
                    <p className="text-sm text-fg-primary font-mono leading-relaxed">
                      {ev.reason}
                    </p>
                    {ev.details && (
                      <pre className="text-xs text-fg-muted font-mono bg-bg-card rounded p-2 overflow-x-auto max-h-24 overflow-y-auto schema-scroll">
                        {JSON.stringify(ev.details, null, 2)}
                      </pre>
                    )}
                  </div>
                ))}
              </div>
            </motion.div>
          )}

          {/* Empty state when analyzed but no evidence */}
          {failureReport && failureReport.evidence.length === 0 && (
            <EmptyState
              icon={Bug}
              message="No failure evidence found"
              description="This trajectory completed without detected failures"
            />
          )}
        </>
      )}

      {/* Empty state when no trajectory selected */}
      {!selectedTrajectoryId && (
        <EmptyState
          icon={Bug}
          message="Select a trajectory to analyze"
          description="Pick a trajectory from the dropdown above to view failure analysis"
        />
      )}
    </motion.div>
  );
}
