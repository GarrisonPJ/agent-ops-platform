import { render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import { baselineRun, cloneFixture } from "../services/recorded/fixtures";
import type { EventEnvelope, Run } from "../types/phase1";
import RunDetailPage from "./RunDetailPage";

const mocks = vi.hoisted(() => ({
  run: undefined as unknown,
  events: [] as unknown[],
  mutation: () => [() => undefined, { isLoading: false }],
  idleQuery: {
    data: undefined,
    error: undefined,
    isFetching: false,
    refetch: () => undefined,
  },
}));

vi.mock("../services/experimentsApi", () => ({
  useGetRunQuery: () => ({
    data: mocks.run,
    error: undefined,
    isLoading: false,
    refetch: vi.fn(),
  }),
  useGetExperimentQuery: () => mocks.idleQuery,
  useGetRunAnalysisQuery: () => mocks.idleQuery,
  useCancelRunMutation: mocks.mutation,
  useReplayPolicyMutation: mocks.mutation,
  useActivatePolicyMutation: mocks.mutation,
  useRejectPolicyMutation: mocks.mutation,
  useSaveRegressionCaseMutation: mocks.mutation,
}));

vi.mock("../hooks/useRunStream", () => ({
  useRunStream: () => ({
    events: mocks.events,
    connection: "closed",
    connectionError: null,
  }),
}));

function event(sequence: number, type: string, payload: Record<string, unknown>): EventEnvelope {
  return {
    run_id: "run-x",
    sequence,
    type,
    payload,
    created_at: "2026-11-03T10:00:00Z",
  } as unknown as EventEnvelope;
}

function renderPage(run: Run, events: EventEnvelope[]) {
  mocks.run = run;
  mocks.events = events;
  return render(
    <MemoryRouter initialEntries={[`/runs/${run.id}`]}>
      <Routes>
        <Route path="/runs/:id" element={<RunDetailPage />} />
      </Routes>
    </MemoryRouter>,
  );
}

describe("RunDetailPage", () => {
  afterEach(() => {
    mocks.run = undefined;
    mocks.events = [];
  });

  it("renders process output from the normalized content field", () => {
    const run = { ...cloneFixture(baselineRun), status: "running" as const };
    renderPage(run, [
      event(1, "process_output", { stream: "stdout", content: "hello from the runner" }),
    ]);
    expect(document.body.textContent).toContain("hello from the runner");
  });

  it("shows Timed out and the timed-out banner for a timed_out run_failed event", () => {
    const run = {
      ...cloneFixture(baselineRun),
      status: "running" as const,
      error: "deadline exceeded",
    };
    renderPage(run, [event(1, "run_failed", { status: "timed_out", failure_kind: "timeout" })]);
    expect(screen.getAllByText("Timed out").length).toBeGreaterThan(0);
    expect(screen.getByText("Run timed out")).toBeTruthy();
    expect(screen.queryByText("Run did not complete")).toBeNull();
  });

  it("prefers a terminal run.status over the last event", () => {
    const run = {
      ...cloneFixture(baselineRun),
      status: "timed_out" as const,
      error: "deadline exceeded",
    };
    renderPage(run, [event(1, "run_failed", {})]);
    expect(screen.getByText("Run timed out")).toBeTruthy();
  });
});
