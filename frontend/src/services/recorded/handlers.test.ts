import { beforeEach, describe, expect, it } from "vitest";
import { agentMetadataSchema, experimentSchema, runSchema } from "../../schemas/phase1";
import { BASELINE_RUN_ID, GOLDEN_EXPERIMENT_ID, candidatePolicy } from "./fixtures";
import { recordedHandler, resetRecordedDemo } from "./handlers";

describe("recorded demo handlers", () => {
  beforeEach(() => resetRecordedDemo());

  it("serves only registered public Agent metadata and returns independent copies", async () => {
    const response = await recordedHandler("GET", "/api/agents");
    if (!("data" in response)) throw new Error("Expected Agent metadata");
    const agents = (response.data as unknown[]).map((agent) => agentMetadataSchema.parse(agent));
    expect(agents.map((agent) => agent.id)).toEqual([
      "fixture-agent.v1",
      "reference-openai-compatible.v1",
    ]);
    for (const agent of agents)
      expect(Object.keys(agent).sort()).toEqual(["description", "id", "kind", "name"]);
    (response.data as { name: string }[])[0].name = "Changed locally";
    expect(await recordedHandler("GET", "/agents")).toEqual({ data: agents });
  });

  it.each([
    { mode: "fixture", agentId: undefined, effectiveId: "fixture-agent.v1" },
    { mode: "provider", agentId: undefined, effectiveId: "reference-openai-compatible.v1" },
    { mode: "provider", agentId: "fixture-agent.v1", effectiveId: "fixture-agent.v1" },
    {
      mode: "fixture",
      agentId: "reference-openai-compatible.v1",
      effectiveId: "reference-openai-compatible.v1",
    },
  ])(
    "preserves explicit selection or resolves mode fallback: $mode / $agentId",
    async ({ mode, agentId, effectiveId }) => {
      const createdResponse = await recordedHandler("POST", "/experiments", {
        name: "Agent choice",
        execution_mode: mode,
        ...(agentId ? { agent_id: agentId } : {}),
      });
      if (!("data" in createdResponse)) throw new Error("Expected Experiment");
      const experiment = experimentSchema.parse(createdResponse.data);
      expect(experiment.agent_id).toBe(agentId ?? null);
      const baseline = await recordedHandler("POST", `/experiments/${experiment.id}/runs`, {});
      if (!("data" in baseline)) throw new Error("Expected Run");
      expect(runSchema.parse(baseline.data).evaluation_spec.agent_id).toBe(effectiveId);
      await recordedHandler("POST", `/policies/${candidatePolicy.id}/replay`);
      const detail = await recordedHandler("GET", `/experiments/${experiment.id}`);
      if (!("data" in detail)) throw new Error("Expected Experiment");
      const replay = experimentSchema.parse(detail.data).runs.find((run) => run.kind === "replay");
      expect(replay?.evaluation_spec.agent_id).toBe(effectiveId);
    },
  );

  it("rejects unknown explicit Agents instead of using a mode default", async () => {
    expect(
      await recordedHandler("POST", "/experiments", { agent_id: "unknown-agent.v1" }),
    ).toMatchObject({
      error: {
        status: 400,
        data: {
          code: "AGENT_NOT_REGISTERED",
          message: "Agent is not registered",
          details: { agent_id: "unknown-agent.v1" },
        },
      },
    });
  });

  it("serves a Zod-validated recording of the real Golden scenario", async () => {
    const response = await recordedHandler("GET", "/experiments");
    expect("data" in response).toBe(true);
    if ("data" in response) {
      expect(response.data).toEqual(
        expect.arrayContaining([
          expect.objectContaining({
            id: GOLDEN_EXPERIMENT_ID,
            scenario_id: "checkout-api-latency.v1",
          }),
        ]),
      );
    }
  });

  it("returns the fixed persisted analysis without recomputing it", async () => {
    const response = await recordedHandler("GET", `/runs/${BASELINE_RUN_ID}/analysis`);
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
    const createdResponse = await recordedHandler("POST", "/experiments", {
      name: "Recorded journey",
      task: "Investigate checkout API latency",
      scenario_id: "checkout-api-latency.v1",
    });
    expect("data" in createdResponse).toBe(true);
    if (!("data" in createdResponse)) return;
    const experimentId = (createdResponse.data as { id: string }).id;

    const baselineResponse = await recordedHandler("POST", `/experiments/${experimentId}/runs`, {});
    expect(baselineResponse).toMatchObject({
      data: {
        status: "failed",
        score: -0.01221,
        evaluation_spec: { scenario_id: "checkout-api-latency.v1", seed: 42 },
      },
    });

    const candidateDetail = await recordedHandler("GET", `/experiments/${experimentId}`);
    expect(candidateDetail).toMatchObject({
      data: {
        candidate_policy: {
          status: "candidate",
          replay_run_id: null,
          score_delta: null,
        },
      },
    });

    const replayResponse = await recordedHandler("POST", `/policies/${candidatePolicy.id}/replay`);
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
    const activeDetail = await recordedHandler("GET", `/experiments/${experimentId}`);
    expect(activeDetail).toMatchObject({
      data: {
        active_policy: { status: "active" },
        candidate_policy: null,
      },
    });
  });

  it("saves a fixture failure and gates activation on its regression replay", async () => {
    const createdResponse = await recordedHandler("POST", "/experiments", {
      name: "Recorded regression",
      agent_id: "reference-openai-compatible.v1",
      task: "Investigate checkout API latency",
      scenario_id: "checkout-api-latency.v1",
    });
    if (!("data" in createdResponse)) return;
    const experimentId = (createdResponse.data as { id: string }).id;
    const baselineResponse = await recordedHandler("POST", `/experiments/${experimentId}/runs`, {});
    if (!("data" in baselineResponse)) return;
    const runId = (baselineResponse.data as { id: string }).id;
    expect(runSchema.parse(baselineResponse.data).evaluation_spec.agent_id).toBe(
      "reference-openai-compatible.v1",
    );

    const saved = await recordedHandler("POST", `/runs/${runId}/regression-case`);
    expect(saved).toMatchObject({
      data: {
        source_run_id: runId,
        reproducibility: "deterministic",
        eligible_for_replay: true,
      },
    });
    expect(await recordedHandler("POST", `/runs/${runId}/regression-case`)).toEqual(saved);

    await recordedHandler("POST", `/policies/${candidatePolicy.id}/replay`);
    const gated = await recordedHandler("POST", `/policies/${candidatePolicy.id}/activate`);
    expect(gated).toMatchObject({
      error: { status: 409, data: { code: "REGRESSION_REPLAY_PENDING" } },
    });
    expect(await recordedHandler("GET", `/experiments/${experimentId}`)).toMatchObject({
      data: {
        regression_cases: [
          {
            replay_policy_id: candidatePolicy.id,
            replay_run_id: expect.stringContaining("recorded-regression-replay"),
          },
        ],
      },
    });
    const detail = await recordedHandler("GET", `/experiments/${experimentId}`);
    if (!("data" in detail)) throw new Error("Expected Experiment");
    const regressionReplay = experimentSchema
      .parse(detail.data)
      .runs.find((run) => run.id.startsWith("recorded-regression-replay"));
    expect(regressionReplay?.evaluation_spec.agent_id).toBe("reference-openai-compatible.v1");
    expect(await recordedHandler("POST", `/policies/${candidatePolicy.id}/activate`)).toMatchObject(
      { data: { status: "active" } },
    );
  });

  it("keeps the selected provider mode as recorded response data", async () => {
    const createdResponse = await recordedHandler("POST", "/experiments", {
      name: "Provider recording",
      task: "Investigate checkout API latency",
      scenario_id: "checkout-api-latency.v1",
      execution_mode: "provider",
    });
    expect(createdResponse).toMatchObject({
      data: { execution_mode: "provider" },
    });
    if (!("data" in createdResponse)) return;
    const experimentId = (createdResponse.data as { id: string }).id;

    const baselineResponse = await recordedHandler("POST", `/experiments/${experimentId}/runs`, {});
    expect(baselineResponse).toMatchObject({
      data: { evaluation_spec: { execution_mode: "provider" } },
    });
    if (!("data" in baselineResponse)) return;
    const runId = (baselineResponse.data as { id: string }).id;
    expect(await recordedHandler("POST", `/runs/${runId}/regression-case`)).toMatchObject({
      data: {
        reproducibility: "provider_boundary",
        eligible_for_replay: false,
        reproducibility_note: expect.stringContaining("cannot be completely reproduced"),
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
