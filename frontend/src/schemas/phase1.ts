import { z } from "zod";


export const runStatusSchema = z.enum([
  "queued",
  "claimed",
  "running",
  "cancelling",
  "succeeded",
  "failed",
  "cancelled",
  "timed_out",
]);

export const policyStatusSchema = z.enum([
  "candidate",
  "replaying",
  "validated",
  "rejected",
  "active",
  "superseded",
]);

export const policyPatchSchema = z
  .object({
    instruction_patch: z.array(z.string().min(1).max(500)).max(10),
    tool_priority: z.record(z.string(), z.number().min(0).max(1)),
    max_steps: z.number().int().min(3).max(20),
  })
  .strict();

export const evaluationSpecSchema = z
  .object({
    schema_version: z.literal(1),
    run_id: z.string().min(1),
    experiment_id: z.string().min(1),
    scenario_id: z.literal("checkout-api-latency"),
    task: z.string().min(1).max(4_000),
    seed: z.number().int().min(0).max(2_147_483_647),
    policy: policyPatchSchema.nullable(),
    limits: z
      .object({
        timeout_ms: z.number().int().min(1_000).max(300_000),
        max_output_bytes: z.number().int().min(1_024).max(10_485_760),
      })
      .strict(),
  })
  .strict();

export const runMetricsSchema = z
  .object({
    steps: z.number().int().nonnegative().optional(),
    step_count: z.number().int().nonnegative().optional(),
    latency_ms: z.number().nonnegative().optional(),
    token_prompt: z.number().int().nonnegative().optional(),
    token_completion: z.number().int().nonnegative().optional(),
    total_tokens: z.number().int().nonnegative().optional(),
    tool_failures: z.number().int().nonnegative().optional(),
  })
  .catchall(z.unknown());

export const runSchema = z
  .object({
    id: z.string().min(1),
    experiment_id: z.string().min(1),
    kind: z.enum(["baseline", "replay"]),
    source_run_id: z.string().nullable(),
    policy_id: z.string().nullable(),
    status: runStatusSchema,
    score: z.number().nullable(),
    metrics: runMetricsSchema,
    evaluation_spec: evaluationSpecSchema,
    error: z.string().nullable(),
    queued_at: z.string().min(1),
    started_at: z.string().nullable(),
    completed_at: z.string().nullable(),
  })
  .strict();

export const policySchema = z
  .object({
    id: z.string().min(1),
    experiment_id: z.string().min(1),
    source_run_id: z.string().min(1),
    parent_policy_id: z.string().nullable(),
    replay_run_id: z.string().nullable(),
    status: policyStatusSchema,
    patch: policyPatchSchema,
    rationale: z.string().min(1),
    score_delta: z.number().nullable(),
    reject_reason: z.string().nullable(),
    created_at: z.string().min(1),
  })
  .strict();

export const experimentSchema = z
  .object({
    id: z.string().min(1),
    name: z.string().min(1),
    task: z.string().min(1),
    scenario_id: z.literal("checkout-api-latency"),
    created_at: z.string().min(1),
    runs: z.array(runSchema),
    active_policy: policySchema.nullable(),
    candidate_policy: policySchema.nullable(),
  })
  .strict();

export const failureEvidenceSchema = z
  .object({
    dimension: z.string().min(1),
    step_index: z.number().int().nullable(),
    reason: z.string().min(1),
    severity: z.number().min(0).max(1),
    details: z.record(z.string(), z.unknown()).nullable(),
  })
  .strict();

export const runAnalysisSchema = z
  .object({
    run_id: z.string().min(1),
    dimensions: z.record(z.string(), z.number().min(0).max(1)),
    evidence: z.array(failureEvidenceSchema),
    dominant_type: z.string().nullable(),
    failure_rate: z.number().min(0).max(1),
  })
  .strict();

export const eventEnvelopeSchema = z
  .object({
    schema_version: z.literal(1),
    run_id: z.string().min(1),
    sequence: z.number().int().positive(),
    occurred_at: z.string().min(1),
    type: z.enum([
      "run_started",
      "step_completed",
      "process_output",
      "run_completed",
      "run_failed",
      "run_cancelled",
    ]),
    payload: z.record(z.string(), z.unknown()),
  })
  .strict();

export const recordedJourneySchema = z
  .object({
    experiment: experimentSchema,
    secondary_experiment: experimentSchema,
    baseline_analysis: runAnalysisSchema,
    replay_analysis: runAnalysisSchema,
    events: z.record(z.string(), z.array(eventEnvelopeSchema)),
  })
  .strict();

export type RunStatus = z.infer<typeof runStatusSchema>;
export type PolicyStatus = z.infer<typeof policyStatusSchema>;
export type PolicyPatch = z.infer<typeof policyPatchSchema>;
export type EvaluationSpec = z.infer<typeof evaluationSpecSchema>;
export type RunMetrics = z.infer<typeof runMetricsSchema>;
export type Run = z.infer<typeof runSchema>;
export type Policy = z.infer<typeof policySchema>;
export type Experiment = z.infer<typeof experimentSchema>;
export type FailureEvidence = z.infer<typeof failureEvidenceSchema>;
export type RunAnalysis = z.infer<typeof runAnalysisSchema>;
export type EventEnvelope = z.infer<typeof eventEnvelopeSchema>;
