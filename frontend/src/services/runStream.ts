import { eventEnvelopeSchema } from "../schemas/phase1";
import type { EventEnvelope } from "../types/phase1";
import { subscribeToRecordedRun } from "./recorded/stream";

const IS_RECORDED_DEMO = import.meta.env.VITE_MOCK_API === "true";

export interface RunStreamCallbacks {
  onOpen: () => void;
  onEvent: (event: EventEnvelope) => void;
  onDisconnect: (message: string) => void;
}

const TERMINAL_EVENTS = new Set(["run_completed", "run_failed", "run_cancelled"]);

export function parseRunEvent(value: string): EventEnvelope | null {
  try {
    const parsed = eventEnvelopeSchema.safeParse(JSON.parse(value));
    return parsed.success ? parsed.data : null;
  } catch {
    return null;
  }
}

export function subscribeToRunStream(runId: string, after: number, callbacks: RunStreamCallbacks) {
  if (IS_RECORDED_DEMO) {
    return subscribeToRecordedRun(runId, after, callbacks);
  }

  const source = new EventSource(`/api/runs/${encodeURIComponent(runId)}/stream?after=${after}`);
  const eventTypes = eventEnvelopeSchema.shape.type.options;
  let closed = false;
  const close = () => {
    if (closed) return;
    closed = true;
    for (const type of eventTypes) source.removeEventListener(type, onMessage);
    source.onopen = null;
    source.onmessage = null;
    source.onerror = null;
    source.close();
  };
  source.onopen = callbacks.onOpen;
  const onMessage = (message: Event) => {
    if (!(message instanceof MessageEvent)) return;
    const event = parseRunEvent(message.data);
    if (!event) {
      console.warn("Dropped invalid run event", message.data);
      return;
    }
    callbacks.onEvent(event);
    if (TERMINAL_EVENTS.has(event.type)) close();
  };
  source.onmessage = onMessage;
  for (const type of eventTypes) source.addEventListener(type, onMessage);
  source.onerror = () => {
    close();
    callbacks.onDisconnect("The event stream was interrupted.");
  };

  return close;
}
