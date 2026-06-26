import { useState } from "react";
import { motion } from "framer-motion";
import {
  Shield,
  Thermometer,
} from "lucide-react";
import PolicyTimeline from "../../components/PolicyTimeline";
import PolicyDetailPanel from "../../components/PolicyDetailPanel";
import PolicyReviewQueue from "../../components/PolicyReviewQueue";
import {
  useGetPoliciesQuery,
  useGetActivePolicyQuery,
  useGetWarmupStatusQuery,
} from "../../services/api";

export default function PoliciesTab() {
  // ── state ─────────────────────────────────────────────────────────
  const [selectedPolicyId, setSelectedPolicyId] = useState<string | null>(null);

  // ── API hooks ────────────────────────────────────────────────────
  const { data: policies, isLoading: policiesLoading } = useGetPoliciesQuery();
  const {
    data: pendingPolicies,
    isLoading: pendingLoading,
    isError: pendingError,
    error: pendingErrorObj,
    refetch: pendingRefetch,
  } = useGetPoliciesQuery({ status: "pending_review" });
  const { data: activePolicy, isLoading: activePolicyLoading } =
    useGetActivePolicyQuery();
  const { data: warmupStatus } = useGetWarmupStatusQuery();

  return (
    <motion.div
      initial={{ opacity: 0, y: 12 }}
      animate={{ opacity: 1, y: 0 }}
      className="space-y-6"
    >
      {/* Warmup indicator (when no active policy) */}
      {!activePolicyLoading &&
        !activePolicy &&
        warmupStatus &&
        !warmupStatus.ready && (
          <motion.div
            initial={{ opacity: 0, y: 8 }}
            animate={{ opacity: 1, y: 0 }}
            className="bg-bg-card border border-border rounded-lg p-5 flex items-center gap-4"
          >
            <span className="flex items-center justify-center w-10 h-10 rounded-xl bg-accent-amber/10 ring-1 ring-accent-amber/20 flex-shrink-0">
              <Thermometer className="w-5 h-5 text-accent-amber" />
            </span>
            <div>
              <p className="text-xs font-mono text-fg-muted uppercase tracking-wider">
                Warming Up
              </p>
              <p className="text-sm font-mono text-accent-amber font-semibold mt-0.5">
                {warmupStatus.total_trajectories}/
                {warmupStatus.threshold} trajectories collected
              </p>
            </div>
          </motion.div>
        )}

      {/* Timeline */}
      <PolicyTimeline
        policies={policies ?? []}
        selectedId={selectedPolicyId}
        onSelect={(id) => setSelectedPolicyId(id)}
        loading={policiesLoading}
      />

      {/* Review queue */}
      <PolicyReviewQueue
        policies={pendingPolicies ?? []}
        isLoading={pendingLoading}
        isError={pendingError}
        error={pendingErrorObj}
        onRetry={pendingRefetch}
      />

      {/* Detail panel */}
      {selectedPolicyId && policies ? (
        <PolicyDetailPanel
          policy={
            policies.find(
              (p) => p.version_id === selectedPolicyId,
            ) ?? null
          }
          loading={false}
        />
      ) : (
        <PolicyDetailPanel policy={null} loading={false} />
      )}
    </motion.div>
  );
}
