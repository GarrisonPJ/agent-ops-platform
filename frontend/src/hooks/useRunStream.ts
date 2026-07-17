import { useEffect, useRef, useState } from "react";
import { subscribeToRunStream } from "../services/runStream";
import type { EventEnvelope } from "../types/phase1";

export type StreamConnection =
  | "connecting"
  | "connected"
  | "reconnecting"
  | "closed"
  | "error";

const TERMINAL_EVENTS = new Set([
  "run_completed",
  "run_failed",
  "run_cancelled",
]);

export function appendUniqueEvent(
  events: EventEnvelope[],
  incoming: EventEnvelope,
) {
  if (events.some((event) => event.sequence === incoming.sequence)) return events;
  return [...events, incoming].sort((a, b) => a.sequence - b.sequence);
}

export function useRunStream(runId: string | undefined) {
  const [events, setEvents] = useState<EventEnvelope[]>([]);
  const [connection, setConnection] = useState<StreamConnection>("connecting");
  const [connectionError, setConnectionError] = useState<string | null>(null);
  const lastSequence = useRef(0);

  useEffect(() => {
    setEvents([]);
    setConnection("connecting");
    setConnectionError(null);
    lastSequence.current = 0;
  }, [runId]);

  useEffect(() => {
    if (!runId) return;

    let active = true;
    let terminal = false;
    let retryCount = 0;
    let retryTimer: ReturnType<typeof setTimeout> | undefined;
    let unsubscribe: (() => void) | undefined;

    const connect = () => {
      if (!active || terminal) return;
      setConnection(retryCount > 0 ? "reconnecting" : "connecting");
      unsubscribe = subscribeToRunStream(runId, lastSequence.current, {
        onOpen: () => {
          if (!active) return;
          retryCount = 0;
          setConnection("connected");
          setConnectionError(null);
        },
        onEvent: (event) => {
          if (!active || event.run_id !== runId) return;
          lastSequence.current = Math.max(lastSequence.current, event.sequence);
          setEvents((current) => appendUniqueEvent(current, event));
          if (TERMINAL_EVENTS.has(event.type)) {
            terminal = true;
            setConnection("closed");
          }
        },
        onDisconnect: (message) => {
          if (!active || terminal) return;
          retryCount += 1;
          setConnectionError(message);
          setConnection("reconnecting");
          const delay = Math.min(500 * 2 ** (retryCount - 1), 5000);
          retryTimer = setTimeout(connect, delay);
        },
      });
    };

    connect();
    return () => {
      active = false;
      if (retryTimer) clearTimeout(retryTimer);
      unsubscribe?.();
    };
  }, [runId]);

  return { events, connection, connectionError, lastSequence: lastSequence.current };
}
