import { beforeEach, describe, expect, it } from "vitest";
import {
  BASELINE_RUN_ID,
  GOLDEN_EXPERIMENT_ID,
  candidatePolicy,
} from "./fixtures";
import { recordedHandler, resetRecordedDemo } from "./handlers";


describe("recorded demo handlers", () => {
  beforeEach(() => resetRecordedDemo());

  it("serves a Zod-validated recording of the real Golden scenario", async () => {
    const response = await recordedHandler("GET", "/experiments");
    expect("data" in response).toBe(true);
    if ("data" in response) {
      expect(response.data).toEqual(
        expect.arrayContaining([
          expect.objectContaining({
            id: GOLDEN_EXPERIMENT_ID,
            scenario_id: "checkout-api-latency",
          }),
        ]),
      );
    }
  });

  it("returns the fixed persisted analysis without recomputing it", async () => {
    const response = await recordedHandler(
      "GET",
      `/runs/${BASELINE_RUN_ID}/analysis`,
    );
    expect(response).toMatchObject({
      data: {
        run_id: BASELINE_RUN_ID,
        dominant_type: "planning",
        dimensions: {
          planning: 2 / 3,
          budget: 1 / 6,
        },
      },
    });
  });

  it("replays the complete create, baseline, replay, and activate journey", async () => {
    const createdResponse = await recordedHandler(
      "POST",
      "/experiments",
      {
        name: "Recorded journey",
        task: "Investigate checkout API latency",
        scenario_id: "checkout-api-latency",
      },
    );
    expect("data" in createdResponse).toBe(true);
    if (!("data" in createdResponse)) return;
    const experimentId = (createdResponse.data as { id: string }).id;

    const baselineResponse = await recordedHandler(
      "POST",
      `/experiments/${experimentId}/runs`,
      {},
    );
    expect(baselineResponse).toMatchObject({
      data: {
        status: "failed",
        score: -0.01221,
        evaluation_spec: { scenario_id: "checkout-api-latency", seed: 42 },
      },
    });

    const candidateDetail = await recordedHandler(
      "GET",
      `/experiments/${experimentId}`,
    );
    expect(candidateDetail).toMatchObject({
      data: {
        candidate_policy: {
          status: "candidate",
          replay_run_id: null,
          score_delta: null,
        },
      },
    });

    const replayResponse = await recordedHandler(
      "POST",
      `/policies/${candidatePolicy.id}/replay`,
    );
    expect(replayResponse).toMatchObject({
      data: {
        status: "validated",
        score_delta: 1.00797,
      },
    });

    const activationResponse = await recordedHandler(
      "POST",
      `/policies/${candidatePolicy.id}/activate`,
    );
    expect(activationResponse).toMatchObject({
      data: { status: "active" },
    });
    const activeDetail = await recordedHandler(
      "GET",
      `/experiments/${experimentId}`,
    );
    expect(activeDetail).toMatchObject({
      data: {
        active_policy: { status: "active" },
        candidate_policy: null,
      },
    });
  });

  it("reports unknown routes with the shared API error shape", async () => {
    const response = await recordedHandler("GET", "/unknown");
    expect(response).toMatchObject({
      error: {
        status: 404,
        data: { code: "RECORDED_ROUTE_NOT_FOUND" },
      },
    });
  });
});
