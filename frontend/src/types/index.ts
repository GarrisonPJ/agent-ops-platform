export interface Step {
  index: number;
  thought: string;
  action: {
    id: string;
    name: string;
    arguments: Record<string, unknown>;
  } | null;
  observation: string;
  latency_ms: number;
  started_at: string | number;  // float in SSE events, ISO string from DB
  context_window: {
    used: number;
    limit: number;
  };
  token_prompt: number | null;
  token_completion: number | null;
}

export interface TrajectorySummary {
  id: string;
  task: string;
  status: "running" | "success" | "failed";
  step_count: number;
  created_at: string;
  score: number | null;
}

export interface TrajectoryDetail {
  id: string;
  task: string;
  steps: Step[];
  status: "running" | "success" | "failed";
  created_at: string;
  total_tokens: number | null;
  context_window_peak: number | null;
  score: number | null;
  score_breakdown: Record<string, number> | null;
}

export interface RunResponse {
  trajectory_id: string;
  status: string;
}

export interface ToolInfo {
  name: string;
  description: string;
  parameters: Record<string, unknown>;
  enabled: boolean;
}

// ── Compare types ──────────────────────────────────────────────

export interface CompareTrajectoryMeta {
  id: string;
  task: string;
  status: "running" | "success" | "failed";
  created_at: string;
  total_steps: number;
  total_latency_ms: number;
}

export interface CompareStepData {
  index: number;
  thought: string;
  action: { id: string; name: string; arguments: Record<string, unknown> } | null;
  observation: string;
  latency_ms: number;
}

export interface AlignedStep {
  step_index: number;
  trajectories: (CompareStepData | null)[];
  tools_differ: boolean;
  tool_names: (string | null)[];
}

export interface CompareResponse {
  trajectories: CompareTrajectoryMeta[];
  aligned_steps: AlignedStep[];
  max_steps: number;
}

// ── Eval types ──────────────────────────────────────────────

export interface BenchmarkTask {
  name: string;
  task: string;
  description: string;
}

export interface BenchmarkRanking {
  trajectory_id: string;
  rank: number;
  score: number;
  status: string;
}

export interface BenchmarkBestWorst {
  trajectory_id: string;
  score: number;
}

export interface BenchmarkResponse {
  task: string;
  n_runs: number;
  completed: number;
  rankings: BenchmarkRanking[];
  best: BenchmarkBestWorst | null;
  worst: BenchmarkBestWorst | null;
}

export interface ScoreRequest {
  trajectory_id: string;
}

export interface ScoreResponse {
  trajectory_id: string;
  score: number;
  breakdown: Record<string, number>;
}
