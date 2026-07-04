import type { Step } from "../types";
import { createMockEventSource } from "./mock/handlers";

interface DoneEvent {
  type: "done";
  trajectory_id: string;
}

interface ErrorEvent {
  type: "error";
  message: string;
}

type StreamEvent = Step | DoneEvent | ErrorEvent;

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
    const data = JSON.parse(event.data) as StreamEvent;

    if ("type" in data && data.type === "done") {
      onDone();
      eventSource.close();
    } else if ("type" in data && data.type === "error") {
      onError(data.message);
      eventSource.close();
    } else {
      onStep(data as Step);
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
