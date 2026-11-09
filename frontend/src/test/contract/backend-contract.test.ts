/**
 * Frontend half of the backend -> zod drift guard.
 *
 * Parses every entry of backend-responses.json (real backend responses,
 * recorded by backend/phase1_tests/test_frontend_contract_snapshot.py) with the
 * strict schemas the app uses. A backend field the schemas do not know about
 * fails here instead of in the browser.
 */
import { describe, expect, it } from "vitest";
import type { ZodTypeAny } from "zod";

import snapshot from "./backend-responses.json";
import {
  agentMetadataSchema,
  eventEnvelopeSchema,
  experimentSchema,
  policySchema,
  regressionCaseSchema,
  runAnalysisSchema,
  runSchema,
  scenarioSummarySchema,
} from "../../schemas/phase1";

const schemaByEntry: Record<string, ZodTypeAny> = {
  agents: agentMetadataSchema.array(),
  scenarios: scenarioSummarySchema.array(),
  experiments_list: experimentSchema.array(),
  regression_case: regressionCaseSchema,
};

function schemaFor(name: string): ZodTypeAny | undefined {
  if (name in schemaByEntry) return schemaByEntry[name];
  if (name.startsWith("experiment_")) return experimentSchema;
  if (name.startsWith("policy_")) return policySchema;
  if (name.startsWith("run_")) return runSchema;
  if (name.startsWith("analysis_")) return runAnalysisSchema;
  if (name.startsWith("events_")) return eventEnvelopeSchema.array();
  return undefined;
}

describe("backend response snapshot", () => {
  const entries = Object.entries(snapshot as Record<string, unknown>);

  it("covers the main endpoints", () => {
    const names = entries.map(([name]) => name);
    for (const prefix of [
      "agents",
      "scenarios",
      "experiments_list",
      "experiment_",
      "run_",
      "analysis_",
      "policy_",
      "events_",
      "regression_case",
    ]) {
      expect(
        names.some((name) => name.startsWith(prefix)),
        prefix,
      ).toBe(true);
    }
  });

  it.each(entries)("%s parses with its zod schema", (name, body) => {
    const schema = schemaFor(name);
    expect(schema, `no zod schema mapped for snapshot entry ${name}`).toBeDefined();
    const result = schema!.safeParse(body);
    expect(result.success ? null : JSON.stringify(result.error.issues.slice(0, 5))).toBeNull();
  });
});
