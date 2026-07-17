import { eventEnvelopeSchema } from "../schemas/phase1";
import type { EventEnvelope } from "../types/phase1";
import { subscribeToRecordedRun } from "./recorded/stream";


const IS_RECORDED_DEMO = import.meta.env.VITE_MOCK_API === "true";

export interface RunStreamCallbacks {
  onOpen: () => void;
  onEvent: (event: EventEnvelope) => void;
  onDisconnect: (message: string) => void;
}

const TERMINAL_EVENTS = new Set([
  "run_completed",
  "run_failed",
  "run_cancelled",
]);

export function parseRunEvent(value: string): EventEnvelope | null {
  try {
    const parsed = eventEnvelopeSchema.safeParse(JSON.parse(value));
    return parsed.success ? parsed.data : null;
  } catch {
    return null;
  }
}

export function subscribeToRunStream(
  runId: string,
  after: number,
  callbacks: RunStreamCallbacks,
) {
  if (IS_RECORDED_DEMO) {
    return subscribeToRecordedRun(runId, after, callbacks);
  }

  const source = new EventSource(
    `/api/runs/${encodeURIComponent(runId)}/stream?after=${after}`,
  );
  source.onopen = callbacks.onOpen;
  source.onmessage = (message) => {
    const event = parseRunEvent(message.data);
    if (!event) {
      console.warn("Dropped invalid run event", message.data);
      return;
    }
    callbacks.onEvent(event);
    if (TERMINAL_EVENTS.has(event.type)) source.close();
  };
  source.onerror = () => {
    source.close();
    callbacks.onDisconnect("The event stream was interrupted.");
  };

  return () => source.close();
}
