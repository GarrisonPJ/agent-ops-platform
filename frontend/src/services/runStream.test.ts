import { describe, expect, it } from "vitest";
import { parseRunEvent } from "./runStream";

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
