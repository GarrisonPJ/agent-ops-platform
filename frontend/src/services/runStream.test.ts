import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { EventEnvelope } from "../types/phase1";
import { parseRunEvent, subscribeToRunStream } from "./runStream";

describe("parseRunEvent", () => {
  it("accepts a versioned event envelope", () => {
    const event = parseRunEvent(
      JSON.stringify({
        schema_version: 1,
        run_id: "run-1",
        sequence: 4,
        occurred_at: "2026-07-16T08:00:00Z",
        type: "run_completed",
        payload: { score: 0.9 },
      }),
    );
    expect(event?.sequence).toBe(4);
  });

  it("drops malformed or incompatible events", () => {
    expect(parseRunEvent("not-json")).toBeNull();
    expect(
      parseRunEvent(
        JSON.stringify({
          schema_version: 2,
          run_id: "run-1",
          sequence: 1,
          occurred_at: "now",
          type: "run_started",
          payload: {},
        }),
      ),
    ).toBeNull();
  });
});

class TestEventSource extends EventTarget {
  static instances: TestEventSource[] = [];
  onopen: (() => void) | null = null;
  onmessage: ((event: MessageEvent) => void) | null = null;
  onerror: (() => void) | null = null;
  close = vi.fn();

  constructor(readonly url: string) {
    super();
    TestEventSource.instances.push(this);
  }

  override dispatchEvent(event: Event) {
    const result = super.dispatchEvent(event);
    if (event.type === "open") this.onopen?.();
    if (event.type === "message" && event instanceof MessageEvent) this.onmessage?.(event);
    if (event.type === "error") this.onerror?.();
    return result;
  }
}

const envelope = (type: EventEnvelope["type"], sequence: number) =>
  JSON.stringify({
    schema_version: 1,
    run_id: "run-1",
    sequence,
    occurred_at: "2026-10-15T14:00:00Z",
    type,
    payload: {},
  });

describe("subscribeToRunStream", () => {
  const callbacks = () => ({ onOpen: vi.fn(), onEvent: vi.fn(), onDisconnect: vi.fn() });

  beforeEach(() => {
    TestEventSource.instances = [];
    vi.stubGlobal("EventSource", TestEventSource);
  });
  afterEach(() => vi.unstubAllGlobals());

  it("receives named lifecycle, process, and step events", () => {
    const handlers = callbacks();
    subscribeToRunStream("run-1", 0, handlers);
    const source = TestEventSource.instances[0];
    source.dispatchEvent(new Event("open"));
    for (const [index, type] of (
      ["run_started", "process_output", "step_completed"] as const
    ).entries()) {
      source.dispatchEvent(new MessageEvent(type, { data: envelope(type, index + 1) }));
    }
    expect(handlers.onOpen).toHaveBeenCalledOnce();
    expect(handlers.onEvent.mock.calls.map(([event]) => event.type)).toEqual([
      "run_started",
      "process_output",
      "step_completed",
    ]);
  });

  it.each(["run_completed", "run_failed", "run_cancelled"] as const)(
    "closes and removes listeners after %s",
    (type) => {
      const handlers = callbacks();
      subscribeToRunStream("run-1", 0, handlers);
      const source = TestEventSource.instances[0];
      source.dispatchEvent(new MessageEvent(type, { data: envelope(type, 1) }));
      source.dispatchEvent(
        new MessageEvent("step_completed", { data: envelope("step_completed", 2) }),
      );
      source.dispatchEvent(new Event("error"));
      expect(handlers.onEvent).toHaveBeenCalledOnce();
      expect(source.close).toHaveBeenCalledOnce();
      expect(handlers.onDisconnect).not.toHaveBeenCalled();
    },
  );

  it("cleans up a disconnected stream before resuming after the last sequence", () => {
    const handlers = callbacks();
    subscribeToRunStream("run-1", 4, handlers);
    const source = TestEventSource.instances[0];
    expect(source.url).toBe("/api/runs/run-1/stream?after=4");
    source.dispatchEvent(new Event("error"));
    expect(source.close).toHaveBeenCalledOnce();
    expect(handlers.onDisconnect).toHaveBeenCalledOnce();
    subscribeToRunStream("run-1", 5, handlers);
    const resumed = TestEventSource.instances[1];
    expect(resumed.url).toBe("/api/runs/run-1/stream?after=5");
    source.dispatchEvent(
      new MessageEvent("step_completed", { data: envelope("step_completed", 6) }),
    );
    resumed.dispatchEvent(
      new MessageEvent("step_completed", { data: envelope("step_completed", 6) }),
    );
    expect(handlers.onEvent).toHaveBeenCalledOnce();
  });

  it("stops all callbacks when unsubscribed", () => {
    const handlers = callbacks();
    const unsubscribe = subscribeToRunStream("run-1", 0, handlers);
    const source = TestEventSource.instances[0];
    unsubscribe();
    source.dispatchEvent(new Event("open"));
    source.dispatchEvent(
      new MessageEvent("step_completed", { data: envelope("step_completed", 1) }),
    );
    source.dispatchEvent(new Event("error"));
    expect(source.close).toHaveBeenCalledOnce();
    expect(handlers.onOpen).not.toHaveBeenCalled();
    expect(handlers.onEvent).not.toHaveBeenCalled();
    expect(handlers.onDisconnect).not.toHaveBeenCalled();
  });

  it("continues to accept unnamed message events", () => {
    const handlers = callbacks();
    subscribeToRunStream("run-1", 0, handlers);
    TestEventSource.instances[0].dispatchEvent(
      new MessageEvent("message", {
        data: envelope("step_completed", 1),
      }),
    );
    expect(handlers.onEvent).toHaveBeenCalledOnce();
  });
});
