import type { Step } from "../types";
import { StepSchema } from "./sse-schema";
import { createMockEventSource } from "./mock/handlers";

type RawStreamEvent = Record<string, unknown>;

const IS_MOCK = import.meta.env.VITE_MOCK_API === "true";

export function subscribeToAgentStream(
  trajectoryId: string,
  onStep: (step: Step) => void,
  onDone: () => void,
  onError: (message: string) => void,
): () => void {
  /* ── Mock mode: simulate SSE with timers ───────────────────── */
  if (IS_MOCK) {
    return createMockEventSource(trajectoryId, onStep, onDone, onError);
  }

  /* ── Real mode: EventSource ────────────────────────────────── */
  const eventSource = new EventSource(`/api/agents/${trajectoryId}/stream`);

  eventSource.onmessage = (event: MessageEvent) => {
    let data: RawStreamEvent;
    try {
      data = JSON.parse(event.data);
    } catch {
      console.warn("[SSE] Failed to parse event data:", event.data);
      return;
    }

    if (data.type === "done") {
      onDone();
      eventSource.close();
    } else if (data.type === "error") {
      onError(typeof data.message === "string" ? data.message : String(data.message));
      eventSource.close();
    } else {
      const result = StepSchema.safeParse(data);
      if (result.success) {
        onStep(result.data);
      } else {
        console.warn("[SSE] Invalid step payload dropped:", event.data, result.error);
      }
    }
  };

  eventSource.onerror = () => {
    if (eventSource.readyState === EventSource.CLOSED) {
      eventSource.close();
      onError('Connection lost');
    }
  };

  return () => eventSource.close();
}
