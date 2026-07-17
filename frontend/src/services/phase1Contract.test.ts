import { describe, expect, it } from "vitest";
import {
  eventEnvelopeSchema,
  recordedJourneySchema,
} from "../schemas/phase1";
import { goldenJourney } from "./recorded/fixtures";
import { parseRunEvent } from "./runStream";


describe("Phase 1 frontend contract", () => {
  it("validates the complete recorded journey at the module boundary", () => {
    expect(recordedJourneySchema.parse(goldenJourney)).toEqual(goldenJourney);
    for (const events of Object.values(goldenJourney.events)) {
      events.forEach((event, index) => {
        expect(eventEnvelopeSchema.parse(event).sequence).toBe(index + 1);
      });
    }
  });

  it("rejects sequence zero like the Python and Rust contracts", () => {
    expect(
      parseRunEvent(
        JSON.stringify({
          schema_version: 1,
          run_id: "run-1",
          sequence: 0,
          occurred_at: "2026-07-16T08:00:00Z",
          type: "run_started",
          payload: {},
        }),
      ),
    ).toBeNull();
  });
});
