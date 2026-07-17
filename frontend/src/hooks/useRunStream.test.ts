import { describe, expect, it } from "vitest";
import type { EventEnvelope } from "../types/phase1";
import { appendUniqueEvent } from "./useRunStream";

const event = (sequence: number): EventEnvelope => ({
  schema_version: 1,
  run_id: "run-1",
  sequence,
  occurred_at: "2026-07-16T08:00:00Z",
  type: "run_started",
  payload: {},
});

describe("appendUniqueEvent", () => {
  it("deduplicates reconnect playback by sequence", () => {
    const current = [event(1), event(2)];
    expect(appendUniqueEvent(current, event(2))).toBe(current);
  });

  it("keeps events ordered when replay starts after the last sequence", () => {
    expect(appendUniqueEvent([event(1), event(3)], event(2)).map((item) => item.sequence)).toEqual([1, 2, 3]);
  });
});
