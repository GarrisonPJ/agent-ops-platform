import { describe, expect, it } from "vitest";
import type { EventEnvelope, RunAnalysis } from "../types/phase1";
import { compareAnalysis, compareMetric, compareSteps } from "./comparison";

function event(
  sequence: number,
  index: number,
  observation: string,
  extra: Record<string, unknown> = {},
): EventEnvelope {
  return {
    schema_version: 1,
    run_id: "run",
    sequence,
    occurred_at: "2026-08-23T00:00:00Z",
    type: "step_completed",
    payload: {
      index,
      observation,
      ...extra,
    },
  };
}

function analysis(overrides: Partial<RunAnalysis> = {}): RunAnalysis {
  return {
    run_id: "run",
    dimensions: { planning: 0.6 },
    evidence: [],
    dominant_type: null,
    failure_rate: 0.5,
    failure_types: { loop_no_progress: 0.5 },
    metric_directions: { planning: "minimize", failure_rate: "minimize" },
    ...overrides,
  };
}

describe("compareMetric", () => {
  it("uses the optimization direction to classify changes", () => {
    expect(compareMetric(0.4, 0.8, "maximize")).toMatchObject({
      delta: 0.4,
      status: "improved",
    });
    expect(compareMetric(500, 300, "minimize")).toMatchObject({
      delta: -200,
      status: "improved",
    });
    expect(compareMetric(1, 1, "maximize").status).toBe("unchanged");
  });

  it("keeps a missing side neutral instead of inventing a delta", () => {
    expect(compareMetric(undefined, 1, "maximize")).toMatchObject({
      before: null,
      after: 1,
      delta: null,
      status: "unchanged",
    });
  });
});

describe("compareSteps", () => {
  it("identifies added, removed, changed, and unchanged steps", () => {
    const before = [
      event(1, 0, "same", { tool_call: { name: "search" } }),
      event(2, 1, "failed request", { tool_call: { name: "fetch" } }),
      event(3, 3, "removed"),
    ];
    const after = [
      event(1, 0, "same", { tool_call: { name: "search" } }),
      event(2, 1, "recovered", { tool_call: { name: "fetch" } }),
      event(3, 2, "new step"),
    ];

    expect(compareSteps(before, after)).toEqual([
      expect.objectContaining({ index: 0, kind: "unchanged", status: "unchanged" }),
      expect.objectContaining({ index: 1, kind: "changed", status: "improved" }),
      expect.objectContaining({ index: 2, kind: "added", status: "regressed" }),
      expect.objectContaining({ index: 3, kind: "removed", status: "improved" }),
    ]);
  });
});

describe("compareAnalysis", () => {
  it("compares dimensions and classifications independently", () => {
    const result = compareAnalysis(
      analysis({
        dimensions: { planning: 0.8 },
        failure_types: { planning: 0.2 },
        failure_rate: 0.5,
        metric_directions: {
          planning: "minimize",
          failure_rate: "minimize",
        },
      }),
      analysis({
        dimensions: { planning: 0.4 },
        failure_types: { planning: 0.4 },
        failure_rate: 0.2,
        metric_directions: {
          planning: "minimize",
          failure_rate: "minimize",
        },
      }),
    );

    expect(result?.failureRate.status).toBe("improved");
    expect(result?.dimensions.planning.status).toBe("improved");
    expect(result?.failureTypes.planning.status).toBe("regressed");
  });

  it("returns null until both persisted analyses are available", () => {
    expect(compareAnalysis(undefined, analysis())).toBeNull();
  });

  it("treats absent rate dimensions as zero", () => {
    const result = compareAnalysis(
      analysis({ dimensions: { budget: 0.5 }, failure_types: { timeout: 0.25 } }),
      analysis({ dimensions: {}, failure_types: {} }),
    );

    expect(result?.dimensions.budget).toMatchObject({ before: 0.5, after: 0, status: "improved" });
    expect(result?.failureTypes.timeout).toMatchObject({
      before: 0.25,
      after: 0,
      status: "improved",
    });
  });
});
