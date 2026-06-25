import type { Step } from "../types";

interface DoneEvent {
  type: "done";
  trajectory_id: string;
}

interface ErrorEvent {
  type: "error";
  message: string;
}

type StreamEvent = Step | DoneEvent | ErrorEvent;

export function subscribeToAgentStream(
  trajectoryId: string,
  onStep: (step: Step) => void,
  onDone: () => void,
  onError: (message: string) => void,
): () => void {
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
