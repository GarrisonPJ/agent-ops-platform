import { useState, useCallback } from "react";
import {
  useApprovePolicyMutation,
  useRejectPolicyMutation,
} from "../services/api";
import { toast } from "../lib/toast";

export function usePolicyActions() {
  const [approvePolicy, { isLoading: isApproving }] =
    useApprovePolicyMutation();
  const [rejectPolicy] = useRejectPolicyMutation();
  const [rejectingId, setRejectingId] = useState<string | null>(null);

  const approve = useCallback(
    async (versionId: string) => {
      try {
        await approvePolicy(versionId).unwrap();
        toast.success("Policy approved and activated");
      } catch {
        toast.error("Failed to approve policy");
      }
    },
    [approvePolicy],
  );

  const rejectWithReason = useCallback(
    async (versionId: string) => {
      const reason = window.prompt("Reason for rejection (optional):");
      if (reason === null) return;
      setRejectingId(versionId);
      try {
        await rejectPolicy({ version_id: versionId, reason }).unwrap();
        toast.success("Policy rejected");
      } catch {
        toast.error("Failed to reject policy");
      } finally {
        setRejectingId(null);
      }
    },
    [rejectPolicy],
  );

  const reject = useCallback(
    async (versionId: string, reason: string) => {
      setRejectingId(versionId);
      try {
        await rejectPolicy({ version_id: versionId, reason }).unwrap();
        toast.success("Policy rejected");
      } catch {
        toast.error("Failed to reject policy");
      } finally {
        setRejectingId(null);
      }
    },
    [rejectPolicy],
  );

  return {
    approvePolicy: approve,
    rejectWithReason,
    rejectPolicy: reject,
    isApproving,
    rejectingId,
  };
}
