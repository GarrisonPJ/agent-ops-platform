import type { FetchArgs } from "@reduxjs/toolkit/query";
import type { BaseQueryFn } from "@reduxjs/toolkit/query/react";
import type { Experiment, Policy, Run } from "../../types/phase1";
import {
  BASELINE_RUN_ID,
  CANDIDATE_POLICY_ID,
  REPLAY_RUN_ID,
  baselineAnalysis,
  baselineRun,
  candidatePolicy,
  cloneFixture,
  goldenExperiment,
  replayAnalysis,
  replayRun,
  secondaryExperiment,
} from "./fixtures";


type RecordedError = {
  status: number;
  data: { code: string; message: string };
};

let experiments: Experiment[] = [
  cloneFixture(goldenExperiment),
  cloneFixture(secondaryExperiment),
];

const delay = () => new Promise((resolve) => setTimeout(resolve, 120));
const ok = (data: unknown) => ({ data });
const fail = (status: number, code: string, message: string) => ({
  error: { status, data: { code, message } } satisfies RecordedError,
});

function normalizedPath(url: string) {
  const withoutApi = url.startsWith("/api") ? url.slice(4) : url;
  return withoutApi.split("?")[0] || "/";
}

function findExperiment(id: string) {
  return experiments.find((experiment) => experiment.id === id);
}

function replaceCandidate(policy: Policy) {
  experiments = experiments.map((experiment) =>
    experiment.id === policy.experiment_id
      ? { ...experiment, candidate_policy: cloneFixture(policy) }
      : experiment,
  );
}

export async function recordedHandler(
  method: string,
  url: string,
  body?: unknown,
) {
  await delay();
  const path = normalizedPath(url);

  if (method === "GET" && path === "/experiments") {
    return ok(cloneFixture(experiments));
  }

  if (method === "POST" && path === "/experiments") {
    const request = body as { name?: string; task?: string };
    const created: Experiment = {
      id: `exp-recorded-${experiments.length + 1}`,
      name: request.name?.trim() || "Recorded experiment",
      task: request.task?.trim() || goldenExperiment.task,
      scenario_id: "checkout-api-latency",
      created_at: "2026-07-16T09:00:00Z",
      runs: [],
      active_policy: null,
      candidate_policy: null,
    };
    experiments = [created, ...experiments];
    return ok(cloneFixture(created));
  }

  const experimentRunMatch = path.match(/^\/experiments\/([^/]+)\/runs$/);
  if (method === "POST" && experimentRunMatch) {
    const experiment = findExperiment(experimentRunMatch[1]);
    if (!experiment) {
      return fail(404, "EXPERIMENT_NOT_FOUND", "Experiment not found.");
    }
    const run: Run = {
      ...cloneFixture(baselineRun),
      experiment_id: experiment.id,
      evaluation_spec: {
        ...baselineRun.evaluation_spec,
        experiment_id: experiment.id,
        task: experiment.task,
      },
    };
    const policy: Policy = {
      ...cloneFixture(candidatePolicy),
      experiment_id: experiment.id,
      source_run_id: run.id,
      replay_run_id: null,
      status: "candidate",
      score_delta: null,
      reject_reason: null,
    };
    experiments = experiments.map((item) =>
      item.id === experiment.id
        ? {
            ...item,
            runs: [run, ...item.runs.filter((candidate) => candidate.id !== run.id)],
            candidate_policy: policy,
          }
        : item,
    );
    return ok(cloneFixture(run));
  }

  const experimentMatch = path.match(/^\/experiments\/([^/]+)$/);
  if (method === "GET" && experimentMatch) {
    const experiment = findExperiment(experimentMatch[1]);
    return experiment
      ? ok(cloneFixture(experiment))
      : fail(404, "EXPERIMENT_NOT_FOUND", "Experiment not found.");
  }

  const runAnalysisMatch = path.match(/^\/runs\/([^/]+)\/analysis$/);
  if (method === "GET" && runAnalysisMatch) {
    const id = runAnalysisMatch[1];
    if (id === BASELINE_RUN_ID) return ok(cloneFixture(baselineAnalysis));
    if (id === REPLAY_RUN_ID) return ok(cloneFixture(replayAnalysis));
    return fail(404, "ANALYSIS_NOT_FOUND", "Analysis not found.");
  }

  const cancelMatch = path.match(/^\/runs\/([^/]+)\/cancel$/);
  if (method === "POST" && cancelMatch) {
    const run = experiments
      .flatMap((item) => item.runs)
      .find((item) => item.id === cancelMatch[1]);
    if (!run) return fail(404, "RUN_NOT_FOUND", "Run not found.");
    const cancelled: Run = {
      ...cloneFixture(run),
      status: "cancelled",
      completed_at: "2026-07-16T09:00:03Z",
    };
    experiments = experiments.map((experiment) => ({
      ...experiment,
      runs: experiment.runs.map((item) =>
        item.id === cancelled.id ? cancelled : item,
      ),
    }));
    return ok(cloneFixture(cancelled));
  }

  const runMatch = path.match(/^\/runs\/([^/]+)$/);
  if (method === "GET" && runMatch) {
    const id = runMatch[1];
    const run = experiments
      .flatMap((item) => item.runs)
      .find((item) => item.id === id);
    if (run) return ok(cloneFixture(run));
    if (id === BASELINE_RUN_ID) return ok(cloneFixture(baselineRun));
    if (id === REPLAY_RUN_ID) return ok(cloneFixture(replayRun));
    return fail(404, "RUN_NOT_FOUND", "Run not found.");
  }

  const replayMatch = path.match(/^\/policies\/([^/]+)\/replay$/);
  if (method === "POST" && replayMatch) {
    const current = experiments
      .map((item) => item.candidate_policy)
      .find((policy) => policy?.id === replayMatch[1]);
    if (!current && replayMatch[1] !== CANDIDATE_POLICY_ID) {
      return fail(404, "POLICY_NOT_FOUND", "Policy not found.");
    }
    const source = current ?? cloneFixture(candidatePolicy);
    const policy: Policy = {
      ...source,
      status: "validated",
      replay_run_id: REPLAY_RUN_ID,
      score_delta: candidatePolicy.score_delta,
    };
    const experiment = findExperiment(policy.experiment_id);
    if (!experiment) {
      return fail(404, "EXPERIMENT_NOT_FOUND", "Experiment not found.");
    }
    const recordedReplay: Run = {
      ...cloneFixture(replayRun),
      experiment_id: experiment.id,
      source_run_id: policy.source_run_id,
      policy_id: policy.id,
      evaluation_spec: {
        ...replayRun.evaluation_spec,
        experiment_id: experiment.id,
        task: experiment.task,
        policy: policy.patch,
      },
    };
    replaceCandidate(policy);
    experiments = experiments.map((item) =>
      item.id === policy.experiment_id &&
      !item.runs.some((run) => run.id === recordedReplay.id)
        ? { ...item, runs: [recordedReplay, ...item.runs] }
        : item,
    );
    return ok(cloneFixture(policy));
  }

  const activateMatch = path.match(/^\/policies\/([^/]+)\/activate$/);
  if (method === "POST" && activateMatch) {
    const experiment = experiments.find(
      (item) => item.candidate_policy?.id === activateMatch[1],
    );
    if (!experiment?.candidate_policy) {
      return fail(404, "POLICY_NOT_FOUND", "Policy not found.");
    }
    const active: Policy = {
      ...experiment.candidate_policy,
      status: "active",
    };
    experiments = experiments.map((item) =>
      item.id === experiment.id
        ? { ...item, active_policy: active, candidate_policy: null }
        : item,
    );
    return ok(cloneFixture(active));
  }

  const rejectMatch = path.match(/^\/policies\/([^/]+)\/reject$/);
  if (method === "POST" && rejectMatch) {
    const experiment = experiments.find(
      (item) => item.candidate_policy?.id === rejectMatch[1],
    );
    if (!experiment?.candidate_policy) {
      return fail(404, "POLICY_NOT_FOUND", "Policy not found.");
    }
    const rejected: Policy = {
      ...experiment.candidate_policy,
      status: "rejected",
      reject_reason: "Rejected in the recorded preview.",
    };
    experiments = experiments.map((item) =>
      item.id === experiment.id ? { ...item, candidate_policy: null } : item,
    );
    return ok(cloneFixture(rejected));
  }

  return fail(
    404,
    "RECORDED_ROUTE_NOT_FOUND",
    `No recorded response for ${method} ${path}.`,
  );
}

export const recordedBaseQuery: BaseQueryFn<
  string | FetchArgs,
  unknown,
  RecordedError
> = async (args) => {
  const url = typeof args === "string" ? args : args.url;
  const method = typeof args === "string" ? "GET" : (args.method ?? "GET");
  const body = typeof args === "string" ? undefined : args.body;
  return recordedHandler(method, url, body);
};

export function resetRecordedDemo() {
  experiments = [
    cloneFixture(goldenExperiment),
    cloneFixture(secondaryExperiment),
  ];
}
