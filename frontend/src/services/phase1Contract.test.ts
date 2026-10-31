import { configureStore } from "@reduxjs/toolkit";
import { afterEach, describe, expect, it, vi } from "vitest";
import {
  agentMetadataSchema,
  evaluationSpecSchema,
  eventEnvelopeSchema,
  experimentSchema,
  recordedJourneySchema,
} from "../schemas/phase1";
import { goldenJourney, registeredAgents } from "./recorded/fixtures";
import { experimentsApi } from "./experimentsApi";
import { recordedHandler, resetRecordedDemo } from "./recorded/handlers";
import { parseRunEvent } from "./runStream";

const transport = vi.hoisted(() => vi.fn());
vi.mock("@reduxjs/toolkit/query/react", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@reduxjs/toolkit/query/react")>()),
  fetchBaseQuery: () => transport,
}));
afterEach(() => transport.mockReset());

describe("Phase 1 frontend contract", () => {
  it("fetches and caches only public Agent metadata through RTK Query", async () => {
    transport.mockResolvedValue({ data: registeredAgents });
    const store = configureStore({
      reducer: { [experimentsApi.reducerPath]: experimentsApi.reducer },
      middleware: (getDefaultMiddleware) =>
        getDefaultMiddleware().concat(experimentsApi.middleware),
    });
    try {
      expect(await store.dispatch(experimentsApi.endpoints.getAgents.initiate()).unwrap()).toEqual(
        registeredAgents,
      );
      await store.dispatch(experimentsApi.endpoints.getAgents.initiate()).unwrap();
      expect(transport).toHaveBeenCalledTimes(1);
      expect(transport.mock.calls[0][0]).toBe("/agents");
    } finally {
      store.dispatch(experimentsApi.util.resetApiState());
    }
  });

  it("propagates registry failures rather than returning fixture entries", async () => {
    transport.mockResolvedValue({
      error: {
        status: 503,
        data: { code: "AGENT_REGISTRY_INVALID", message: "Agent registry is invalid" },
      },
    });
    const store = configureStore({
      reducer: { [experimentsApi.reducerPath]: experimentsApi.reducer },
      middleware: (getDefaultMiddleware) =>
        getDefaultMiddleware().concat(experimentsApi.middleware),
    });
    try {
      await expect(
        store.dispatch(experimentsApi.endpoints.getAgents.initiate()).unwrap(),
      ).rejects.toMatchObject({ status: 503 });
      expect(experimentsApi.endpoints.getAgents.select()(store.getState()).data).toBeUndefined();
    } finally {
      store.dispatch(experimentsApi.util.resetApiState());
    }
  });

  it.each([undefined, "fixture-agent.v1", "reference-openai-compatible.v1"])(
    "keeps optional Agent %s in the create API payload and parses the response",
    async (agentId) => {
      resetRecordedDemo();
      transport.mockImplementation((args) => recordedHandler(args.method, args.url, args.body));
      const store = configureStore({
        reducer: { [experimentsApi.reducerPath]: experimentsApi.reducer },
        middleware: (getDefaultMiddleware) =>
          getDefaultMiddleware().concat(experimentsApi.middleware),
      });
      const request = {
        name: "Agent contract",
        task: "Investigate checkout",
        scenario_id: "checkout-api-latency.v2",
        execution_mode: "provider" as const,
        ...(agentId ? { agent_id: agentId } : {}),
      };
      try {
        const result = await store
          .dispatch(experimentsApi.endpoints.createExperiment.initiate(request))
          .unwrap();
        expect(transport.mock.calls[0][0]).toEqual({
          url: "/experiments",
          method: "POST",
          body: request,
        });
        expect(result.agent_id).toBe(agentId ?? null);
        if (!agentId) expect(transport.mock.calls[0][0].body).not.toHaveProperty("agent_id");
      } finally {
        store.dispatch(experimentsApi.util.resetApiState());
      }
    },
  );

  it("accepts omitted, nullable and explicit Agent IDs without changing v1 scenario defaults", () => {
    for (const agentId of [undefined, null, "fixture-agent.v1", "reference-openai-compatible.v1"]) {
      expect(
        experimentSchema.parse({ ...goldenJourney.experiment, agent_id: agentId }).agent_id,
      ).toBe(agentId);
      const spec = { ...goldenJourney.experiment.runs[0].evaluation_spec, agent_id: agentId };
      expect(evaluationSpecSchema.parse(spec).agent_id).toBe(agentId);
      const { scenario_id: _scenarioId, ...legacy } = spec;
      expect(evaluationSpecSchema.parse(legacy).scenario_id).toBe("checkout-api-latency.v2");
    }
    for (const agentId of ["", "unversioned", "../command.v1", "a".repeat(65) + ".v1"]) {
      expect(
        experimentSchema.safeParse({ ...goldenJourney.experiment, agent_id: agentId }).success,
      ).toBe(false);
      expect(
        evaluationSpecSchema.safeParse({
          ...goldenJourney.experiment.runs[0].evaluation_spec,
          agent_id: agentId,
        }).success,
      ).toBe(false);
    }
  });

  it("accepts exactly safe public Agent metadata", () => {
    for (const agent of registeredAgents) {
      expect(Object.keys(agent).sort()).toEqual(["description", "id", "kind", "name"]);
      expect(agentMetadataSchema.parse(agent)).toEqual(agent);
    }
    for (const field of ["command", "environment", "url", "model", "credentials"]) {
      expect(
        agentMetadataSchema.safeParse({ ...registeredAgents[0], [field]: "private" }).success,
      ).toBe(false);
    }
    expect(agentMetadataSchema.safeParse({ ...registeredAgents[0], kind: "command" }).success).toBe(
      false,
    );
  });
  it.each([
    ["name", 200],
    ["description", 1000],
  ] as const)("bounds Agent %s by Unicode code points like the API", (field, maximum) => {
    const atLimit = { ...registeredAgents[0], [field]: "😀".repeat(maximum) };
    expect(agentMetadataSchema.parse(atLimit)).toEqual(atLimit);
    for (const value of ["😀".repeat(maximum + 1), "", "secret\u0000text"]) {
      expect(
        agentMetadataSchema.safeParse({ ...registeredAgents[0], [field]: value }).success,
      ).toBe(false);
    }
  });

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

  it("defaults legacy experiment recordings to an empty regression set", () => {
    const { regression_cases: _regressionCases, ...legacy } = goldenJourney.experiment;
    expect(experimentSchema.parse(legacy).regression_cases).toEqual([]);
  });
});
