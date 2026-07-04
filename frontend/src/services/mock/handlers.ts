import type { BaseQueryFn } from "@reduxjs/toolkit/query/react";
import type { FetchArgs } from "@reduxjs/toolkit/query";
import type { BenchmarkResponse, Step } from "../../types";

import {
  mockTrajectories,
  mockTraceDetails,
  mockTools,
  mockBenchmarks,
  mockCompareResponse,
  mockFailureSummary,
  mockFailureReports,
  mockPolicies,
  mockActivePolicy,
  mockWarmupStatus,
} from "./data";

/* ─── Helpers ─────────────────────────────────────────────────── */

function delay(ms: number) {
  return new Promise((r) => setTimeout(r, ms));
}

function parseQueryParams(url: string): Record<string, string> {
  const idx = url.indexOf("?");
  if (idx === -1) return {};
  const qs = url.slice(idx + 1);
  const params: Record<string, string> = {};
  for (const part of qs.split("&")) {
    const [k, v] = part.split("=").map(decodeURIComponent);
    if (k) params[k] = v;
  }
  return params;
}

function jsonResponse(data: unknown) {
  return { data };
}

function errorResponse(status: number, message: string) {
  return { error: { status, data: message } };
}

let toolState = mockTools.map((t) => ({ ...t }));

/* ─── Counter for generating unique trace IDs ──────────────────── */

let runCounter = 0;

/* ─── Request Router ───────────────────────────────────────────── */

export async function mockHandler(
  method: string,
  url: string,
  body?: unknown,
) {
  /* Normalize URL: strip /api prefix */
  const path = url.startsWith("/api") ? url.slice(4) || "/" : url;

  /* Simulate network latency (80–250ms) */
  await delay(80 + Math.random() * 170);

  /* ── Traces ─────────────────────────────────────────────────── */
  if (method === "GET" && path === "/traces") {
    const params = parseQueryParams(url);
    let filtered = [...mockTrajectories];
    if (params.status) {
      filtered = filtered.filter((t) => t.status === params.status);
    }
    if (params.tool) {
      /* In a real app this filters by tool used; for mock we just fuzzy-match task */
      const q = params.tool.toLowerCase();
      filtered = filtered.filter((t) => t.task.toLowerCase().includes(q));
    }
    return jsonResponse({ trajectories: filtered, total: filtered.length });
  }

  if (method === "GET" && path.startsWith("/traces/")) {
    const id = path.replace("/traces/", "").split("?")[0];
    const trace = mockTraceDetails[id];
    if (!trace) return errorResponse(404, `Trace not found: ${id}`);
    return jsonResponse(trace);
  }

  /* ── Agent Run ───────────────────────────────────────────────── */
  if (method === "POST" && path === "/agents/run") {
    const task =
      typeof body === "object" && body !== null
        ? (body as { task?: string }).task ?? "mock task"
        : "mock task";
    runCounter++;
    const newId = `tr-mock${runCounter}`;

    /* Append a running trace so the user can navigate to it */
    const newTrace = {
      id: newId,
      task,
      steps: [] as [],
      status: "running" as const,
      created_at: new Date().toISOString(),
      total_tokens: null,
      context_window_peak: null,
      score: null,
      score_breakdown: null,
    };

    /* Track it (in-memory only for this session) */
    (mockTraceDetails as Record<string, typeof newTrace>)[newId] = newTrace;

    return jsonResponse({ trajectory_id: newId, status: "running" });
  }

  /* ── Agent Cancel ────────────────────────────────────────────── */
  if (method === "POST" && path.includes("/agents/") && path.endsWith("/cancel")) {
    return jsonResponse({ status: "cancelled" });
  }

  /* ── Tools ───────────────────────────────────────────────────── */
  if (method === "GET" && path === "/tools") {
    return jsonResponse(toolState);
  }

  if (method === "PATCH" && path.includes("/tools/") && path.endsWith("/toggle")) {
    const name = path.split("/")[2];
    const tool = toolState.find((t) => t.name === name);
    if (!tool) return errorResponse(404, `Tool not found: ${name}`);
    tool.enabled = !tool.enabled;
    return jsonResponse({ name, enabled: tool.enabled });
  }

  /* ── Compare ─────────────────────────────────────────────────── */
  if (method === "POST" && path === "/compare") {
    return jsonResponse(mockCompareResponse);
  }

  /* ── Eval / Benchmarks ───────────────────────────────────────── */
  if (method === "GET" && path === "/eval/benchmarks") {
    return jsonResponse(mockBenchmarks);
  }

  if (method === "POST" && path === "/eval/benchmark") {
    const nRuns =
      typeof body === "object" && body !== null
        ? (body as { n_runs?: number }).n_runs ?? 3
        : 3;
    const rankings = Array.from({ length: nRuns }, (_, i) => ({
      trajectory_id: `tr-bench-${String.fromCharCode(97 + i)}${i}`,
      rank: i + 1,
      score: Math.round((0.95 - i * 0.15) * 100) / 100,
      status: (i < nRuns - 1 ? "success" : "failed") as "success" | "failed",
    }));
    const result: BenchmarkResponse = {
      task:
        typeof body === "object" && body !== null
          ? (body as { task_name?: string; task?: string }).task_name ??
            (body as { task?: string }).task ??
            "mock benchmark"
          : "mock benchmark",
      n_runs: nRuns,
      completed: nRuns,
      rankings,
      best: rankings[0],
      worst: rankings[rankings.length - 1],
    };
    return jsonResponse(result);
  }

  if (method === "GET" && path.startsWith("/eval/export")) {
    /* Return a JSON blob mimicking the export */
    const blobContent = JSON.stringify({ mock: true, format: "export" });
    return { data: new Blob([blobContent], { type: "application/json" }) };
  }

  if (method === "POST" && path === "/eval/analyze") {
    const trajId =
      typeof body === "object" && body !== null
        ? (body as { trajectory_id?: string }).trajectory_id ?? ""
        : "";
    const report = mockFailureReports[trajId];
    if (report) return jsonResponse(report);
    /* Generate a generic report for unknown traces */
    return jsonResponse({
      dimensions: { planning: 30, execution: 40, context: 20, budget: 10 },
      dominant: "execution",
      evidence: [
        {
          dimension: "execution",
          step_index: 0,
          reason: "Analysis path could be optimized — consider checking dashboards before raw logs",
          severity: 0.50,
          details: null,
        },
      ],
      needs_human_review: false,
    } as const);
  }

  if (method === "GET" && path === "/eval/analysis/summary") {
    return jsonResponse(mockFailureSummary);
  }

  /* ── Policies ────────────────────────────────────────────────── */
  if (method === "GET" && path === "/eval/policies") {
    const params = parseQueryParams(url);
    let filtered = [...mockPolicies];
    if (params.status) {
      filtered = filtered.filter((p) => p.status === params.status);
    }
    return jsonResponse(filtered);
  }

  if (method === "GET" && path === "/eval/policies/active") {
    return jsonResponse(mockActivePolicy);
  }

  if (method === "GET" && path.startsWith("/eval/policies/") && path.endsWith("/warmup-status")) {
    return jsonResponse(mockWarmupStatus);
  }

  if (method === "GET" && path.startsWith("/eval/policies/")) {
    const id = path.replace("/eval/policies/", "").split("?")[0];
    const policy = mockPolicies.find((p) => p.version_id === id);
    if (!policy) return errorResponse(404, `Policy not found: ${id}`);
    return jsonResponse(policy);
  }

  if (method === "POST" && path.includes("/eval/policies/") && path.endsWith("/approve")) {
    const id = path.split("/")[3];
    const policy = mockPolicies.find((p) => p.version_id === id);
    if (!policy) return errorResponse(404, `Policy not found: ${id}`);
    return jsonResponse({ ...policy, status: "active" });
  }

  if (method === "POST" && path.includes("/eval/policies/") && path.endsWith("/reject")) {
    const id = path.split("/")[3];
    const policy = mockPolicies.find((p) => p.version_id === id);
    if (!policy) return errorResponse(404, `Policy not found: ${id}`);
    return jsonResponse({ ...policy, status: "reverted" });
  }

  if (method === "POST" && path === "/eval/policies/compile") {
    return jsonResponse({
      compiled: true,
      policy: {
        version_id: `pol-compiled-${Date.now()}`,
        version_display: "v0.1.0-draft",
        parent_version: null,
        patch: {
          system_prompt_suffix: "\nAuto-compiled from trajectory analysis.",
        },
        rationale: "Compiled from trajectory analysis",
        expected_impact: null,
        confidence: "low" as const,
        status: "pending_review" as const,
        score_delta: null,
        reject_reason: null,
        created_at: new Date().toISOString(),
      },
    });
  }

  /* ── 404 for unmatched routes ────────────────────────────────── */
  return errorResponse(404, `Mock handler: unknown route ${method} ${path}`);
}

/* ─── RTK Query baseQuery wrapper ─────────────────────────────── */

export const mockBaseQuery: BaseQueryFn<
  string | FetchArgs,
  unknown,
  unknown
> = async (args) => {
  const url = typeof args === "string" ? args : args.url;
  const method = typeof args === "string" ? "GET" : (args.method ?? "GET");
  const body = typeof args === "string" ? undefined : args.body;

  try {
    return await mockHandler(method, url, body);
  } catch (e) {
    return {
      error: {
        status: 500,
        data: e instanceof Error ? e.message : "Mock handler error",
      },
    };
  }
};

/* ─── Global fetch override ────────────────────────────────────── */

export function installFetchMock() {
  const originalFetch = window.fetch.bind(window);

  window.fetch = async (
    input: RequestInfo | URL,
    init?: RequestInit,
  ): Promise<Response> => {
    const url = typeof input === "string" ? input : input instanceof URL ? input.toString() : input.url;
    const method = init?.method ?? "GET";

    if (!url.startsWith("/api/")) {
      return originalFetch(input, init);
    }

    const bodyStr = init?.body;
    let body: unknown = undefined;
    if (typeof bodyStr === "string") {
      try {
        body = JSON.parse(bodyStr);
      } catch {
        body = bodyStr;
      }
    }

    const result = await mockHandler(method, url, body);

    /* Narrow the discriminated union */
    if ("error" in result && result.error) {
      return new Response(
        JSON.stringify({ detail: (result as { error: { status: number; data: string } }).error.data }),
        {
          status: (result as { error: { status: number; data: string } }).error.status ?? 500,
          headers: { "Content-Type": "application/json" },
        },
      );
    }

    /* Handle Blob data (export endpoint) */
    const responseData = (result as { data: unknown }).data;
    if (responseData instanceof Blob) {
      return new Response(responseData, {
        status: 200,
        headers: { "Content-Type": "application/octet-stream" },
      });
    }

    return new Response(JSON.stringify(responseData), {
      status: 200,
      headers: { "Content-Type": "application/json" },
    });
  };
}

/* ─── SSE mock ─────────────────────────────────────────────────── */

export function createMockEventSource(
  trajectoryId: string,
  onStep: (step: Step) => void,
  onDone: () => void,
  onError: (message: string) => void,
): () => void {
  /* For an unknown trajectory, simulate a short stream then error */
  const trace = mockTraceDetails[trajectoryId];
  if (!trace) {
    const errTimeout = setTimeout(() => onError("Trace not found"), 100);
    return () => clearTimeout(errTimeout);
  }

  const steps = trace.steps ?? [];
  let stepIndex = 0;
  let cancelled = false;

  const pushNext = () => {
    if (cancelled) return;

    if (stepIndex < steps.length) {
      const step = steps[stepIndex];
      stepIndex++;
      onStep(step);
      /* Simulate realistic inter-step delay: 800–2000ms */
      setTimeout(pushNext, 800 + Math.random() * 1200);
    } else {
      /* All steps delivered — send done */
      onDone();
    }
  };

  /* Start streaming after a short initial delay */
  const startTimeout = setTimeout(pushNext, 400 + Math.random() * 400);

  return () => {
    cancelled = true;
    clearTimeout(startTimeout);
  };
}
