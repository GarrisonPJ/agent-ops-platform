import type {
  EvaluationSpec,
  EventEnvelope,
  Experiment,
  FailureEvidence,
  Policy,
  PolicyPatch,
  PolicyStatus,
  Run,
  RunAnalysis,
  RunMetrics,
  RunStatus,
} from "../schemas/phase1";


export type {
  EvaluationSpec,
  EventEnvelope,
  Experiment,
  FailureEvidence,
  Policy,
  PolicyPatch,
  PolicyStatus,
  Run,
  RunAnalysis,
  RunMetrics,
  RunStatus,
};

export type RunKind = Run["kind"];
export type RunEventType = EventEnvelope["type"];

export interface ToolCall {
  name: string;
  arguments: Record<string, unknown>;
}

export interface StepCompletedPayload {
  index: number;
  decision_summary: string;
  tool_call: ToolCall | null;
  observation: string;
  latency_ms: number;
  token_prompt: number | null;
  token_completion: number | null;
  context_window: { used: number; limit: number } | null;
}

export interface ApiErrorBody {
  code: string;
  message: string;
  details?: unknown;
}

export interface CreateExperimentRequest {
  name: string;
  task: string;
  scenario_id: "checkout-api-latency";
}
