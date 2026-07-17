import type { EventEnvelope } from "../../types/phase1";
import { recordedEvents } from "./fixtures";

interface RecordedCallbacks {
  onOpen: () => void;
  onEvent: (event: EventEnvelope) => void;
  onDisconnect: (message: string) => void;
}

export function subscribeToRecordedRun(
  runId: string,
  after: number,
  callbacks: RecordedCallbacks,
) {
  const events = (recordedEvents[runId] ?? []).filter((event) => event.sequence > after);
  let stopped = false;
  let timer: ReturnType<typeof setTimeout> | undefined;
  let index = 0;

  callbacks.onOpen();

  const emitNext = () => {
    if (stopped) return;
    const event = events[index];
    if (!event) return;
    callbacks.onEvent(structuredClone(event));
    index += 1;
    if (index < events.length) timer = setTimeout(emitNext, 180);
  };

  if (events.length > 0) timer = setTimeout(emitNext, 80);
  else if (!recordedEvents[runId]) callbacks.onDisconnect("Recorded run not found.");

  return () => {
    stopped = true;
    if (timer) clearTimeout(timer);
  };
}
