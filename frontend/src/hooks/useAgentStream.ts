import { useState, useEffect } from "react";
import type { Step } from "../types";
import { subscribeToAgentStream } from "../services/sse";

export function useAgentStream(trajectoryId: string | null) {
  const [steps, setSteps] = useState<Step[]>([]);
  const [done, setDone] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!trajectoryId) return;

    setSteps([]);
    setDone(false);
    setError(null);

    const cleanup = subscribeToAgentStream(
      trajectoryId,
      (step) => setSteps((prev) => [...prev, step]),
      () => setDone(true),
      (msg) => setError(msg),
    );

    return cleanup;
  }, [trajectoryId]);

  return { steps, done, error };
}
