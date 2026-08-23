import type { EventEnvelope, RunAnalysis } from "../types/phase1";

export type MetricDirection = "maximize" | "minimize";
export type ComparisonStatus = "improved" | "regressed" | "unchanged";

export interface MetricComparison {
  before: number | null;
  after: number | null;
  delta: number | null;
  status: ComparisonStatus;
}

export interface StepComparison {
  index: number;
  kind: "added" | "removed" | "changed" | "unchanged";
  status: ComparisonStatus;
  before: Record<string, unknown> | null;
  after: Record<string, unknown> | null;
}

export interface AnalysisComparison {
  failureRate: MetricComparison;
  dimensions: Record<string, MetricComparison>;
  failureTypes: Record<string, MetricComparison>;
}

export function compareMetric(
  before: number | null | undefined,
  after: number | null | undefined,
  direction: MetricDirection,
): MetricComparison {
  const normalizedBefore = typeof before === "number" ? before : null;
  const normalizedAfter = typeof after === "number" ? after : null;
  if (normalizedBefore == null || normalizedAfter == null) {
    return {
      before: normalizedBefore,
      after: normalizedAfter,
      delta: null,
      status: "unchanged",
    };
  }
  const delta = normalizedAfter - normalizedBefore;
  const improved = direction === "maximize" ? delta > 0 : delta < 0;
  const regressed = direction === "maximize" ? delta < 0 : delta > 0;
  return {
    before: normalizedBefore,
    after: normalizedAfter,
    delta,
    status: improved ? "improved" : regressed ? "regressed" : "unchanged",
  };
}

function eventSteps(events: EventEnvelope[]): Record<number, Record<string, unknown>> {
  return Object.fromEntries(
    events
      .filter((event) => event.type === "step_completed")
      .map((event) => {
        const payload = event.payload as Record<string, unknown>;
        const index = typeof payload.index === "number" ? payload.index : event.sequence;
        return [index, payload];
      }),
  );
}

function hasFailure(step: Record<string, unknown> | null): boolean {
  const signals = [step?.observation, step?.error, step?.status].filter(
    (value): value is string => typeof value === "string",
  );
  return /error|exception|timeout|failed|traceback/i.test(signals.join(" "));
}

export function compareSteps(
  beforeEvents: EventEnvelope[],
  afterEvents: EventEnvelope[],
): StepComparison[] {
  const before = eventSteps(beforeEvents);
  const after = eventSteps(afterEvents);
  const indices = [...new Set([...Object.keys(before), ...Object.keys(after)].map(Number))].sort(
    (a, b) => a - b,
  );
  return indices.map((index) => {
    const beforeStep = before[index] ?? null;
    const afterStep = after[index] ?? null;
    if (!beforeStep) {
      return { index, kind: "added", status: "regressed", before: null, after: afterStep };
    }
    if (!afterStep) {
      return { index, kind: "removed", status: "improved", before: beforeStep, after: null };
    }
    const changed = JSON.stringify(beforeStep) !== JSON.stringify(afterStep);
    const beforeFailed = hasFailure(beforeStep);
    const afterFailed = hasFailure(afterStep);
    return {
      index,
      kind: changed ? "changed" : "unchanged",
      status:
        beforeFailed && !afterFailed
          ? "improved"
          : !beforeFailed && afterFailed
            ? "regressed"
            : "unchanged",
      before: beforeStep,
      after: afterStep,
    };
  });
}

export function compareAnalysis(
  before: RunAnalysis | null | undefined,
  after: RunAnalysis | null | undefined,
): AnalysisComparison | null {
  if (!before || !after) return null;

  const compareRecord = (
    beforeValues: Record<string, number>,
    afterValues: Record<string, number>,
  ) => {
    const keys = new Set([...Object.keys(beforeValues), ...Object.keys(afterValues)]);
    return Object.fromEntries(
      [...keys]
        .sort()
        .map((key) => [
          key,
          compareMetric(
            beforeValues[key] ?? 0,
            afterValues[key] ?? 0,
            before.metric_directions[key] ?? after.metric_directions[key] ?? "minimize",
          ),
        ]),
    );
  };

  return {
    failureRate: compareMetric(
      before.failure_rate,
      after.failure_rate,
      before.metric_directions.failure_rate ?? "minimize",
    ),
    dimensions: compareRecord(before.dimensions, after.dimensions),
    failureTypes: compareRecord(before.failure_types, after.failure_types),
  };
}
