import { createApi, fetchBaseQuery } from "@reduxjs/toolkit/query/react";
import type {
  TrajectorySummary,
  TrajectoryDetail,
  RunResponse,
  ToolInfo,
  CompareResponse,
  BenchmarkTask,
  BenchmarkResponse,
  FailureReport,
  FailureSummary,
  PolicyVersion,
  WarmupStatus,
} from "../types";

export const api = createApi({
  reducerPath: "api",
  baseQuery: fetchBaseQuery({ baseUrl: "/api" }),
  tagTypes: ["Traces", "Trace", "Tools", "Policies"],
  endpoints: (builder) => ({
    runAgent: builder.mutation<RunResponse, { task: string }>({
      query: (body) => ({
        url: "/agents/run",
        method: "POST",
        body,
      }),
      invalidatesTags: [{ type: 'Traces' as const }, { type: 'Trace' as const }],
    }),
    getTraces: builder.query<
      { trajectories: TrajectorySummary[]; total: number },
      { status?: string; tool?: string } | void
    >({
      query: (params) => {
        const searchParams = new URLSearchParams();
        if (params && params.status) searchParams.set("status", params.status);
        if (params && params.tool) searchParams.set("tool", params.tool);
        const qs = searchParams.toString();
        return `/traces${qs ? `?${qs}` : ""}`;
      },
      providesTags: ["Traces"],
    }),
    getTrace: builder.query<TrajectoryDetail, string>({
      query: (id) => `/traces/${id}`,
      providesTags: (_result, _error, id) => [{ type: "Trace" as const, id }],
    }),
    getTools: builder.query<ToolInfo[], void>({
      query: () => "/tools",
      providesTags: ["Tools"],
    }),
    toggleTool: builder.mutation<{ name: string; enabled: boolean }, string>({
      query: (name) => ({
        url: `/tools/${name}/toggle`,
        method: "PATCH",
      }),
      invalidatesTags: ["Tools"],
    }),
    compareTrajectories: builder.mutation<
      CompareResponse,
      { trajectory_ids: string[] }
    >({
      query: (body) => ({
        url: "/compare",
        method: "POST",
        body,
      }),
    }),
    getBenchmarks: builder.query<BenchmarkTask[], void>({
      query: () => "/eval/benchmarks",
    }),
    runBenchmark: builder.mutation<
      BenchmarkResponse,
      { task_name?: string; task?: string; n_runs: number }
    >({
      query: (body) => ({
        url: "/eval/benchmark",
        method: "POST",
        body,
      }),
      invalidatesTags: [{ type: 'Traces' as const }, { type: 'Trace' as const }],
    }),
    exportTrajectory: builder.query<
      Blob,
      { format: string; task_name?: string; trajectory_id?: string }
    >({
      queryFn: async (params) => {
        const searchParams = new URLSearchParams();
        searchParams.set("format", params.format);
        if (params.task_name)
          searchParams.set("task_name", params.task_name);
        if (params.trajectory_id)
          searchParams.set("trajectory_id", params.trajectory_id);
        const response = await fetch(
          `/api/eval/export?${searchParams.toString()}`,
        );
        if (!response.ok) {
          const text = await response.text();
          return { error: { status: response.status, data: text } };
        }
        const blob = await response.blob();
        return { data: blob };
      },
    }),

    analyzeTrajectory: builder.mutation<
      FailureReport,
      { trajectory_id: string }
    >({
      query: (body) => ({
        url: "/eval/analyze",
        method: "POST",
        body,
      }),
    }),

    getFailureSummary: builder.query<
      FailureSummary,
      { last_n?: number } | void
    >({
      query: (params) => {
        const searchParams = new URLSearchParams();
        if (params && params.last_n) searchParams.set("last_n", String(params.last_n));
        const qs = searchParams.toString();
        return `/eval/analysis/summary${qs ? `?${qs}` : ""}`;
      },
    }),

    // ── Policy endpoints ──────────────────────────────────────
    getPolicies: builder.query<PolicyVersion[], { status?: string } | void>({
      query: (params) => {
        const searchParams = new URLSearchParams();
        if (params && params.status) searchParams.set("status", params.status);
        const qs = searchParams.toString();
        return `/eval/policies${qs ? `?${qs}` : ""}`;
      },
      providesTags: ["Policies"],
    }),
    getActivePolicy: builder.query<PolicyVersion | null, void>({
      query: () => "/eval/policies/active",
      providesTags: ["Policies"],
    }),
    getPolicyDetail: builder.query<PolicyVersion, string>({
      query: (id) => `/eval/policies/${id}`,
      providesTags: (_r, _e, id) => [{ type: "Policies" as const, id }],
    }),
    approvePolicy: builder.mutation<PolicyVersion, string>({
      query: (id) => ({
        url: `/eval/policies/${id}/approve`,
        method: "POST",
      }),
      invalidatesTags: ["Policies"],
    }),
    rejectPolicy: builder.mutation<
      PolicyVersion,
      { version_id: string; reason: string }
    >({
      query: ({ version_id, reason }) => ({
        url: `/eval/policies/${version_id}/reject`,
        method: "POST",
        body: { reason },
      }),
      invalidatesTags: ["Policies"],
    }),
    compilePolicy: builder.mutation<
      { compiled: boolean; policy?: PolicyVersion; reason?: string },
      { trajectory_id: string }
    >({
      query: (body) => ({
        url: "/eval/policies/compile",
        method: "POST",
        body,
      }),
      invalidatesTags: ["Policies"],
    }),
    getWarmupStatus: builder.query<WarmupStatus, void>({
      query: () => "/eval/policies/warmup-status",
    }),
  }),
});

export const {
  useRunAgentMutation,
  useGetTracesQuery,
  useGetTraceQuery,
  useGetToolsQuery,
  useToggleToolMutation,
  useCompareTrajectoriesMutation,
  useGetBenchmarksQuery,
  useRunBenchmarkMutation,
  useLazyExportTrajectoryQuery,
  useAnalyzeTrajectoryMutation,
  useGetFailureSummaryQuery,
  useGetPoliciesQuery,
  useGetActivePolicyQuery,
  useGetPolicyDetailQuery,
  useApprovePolicyMutation,
  useRejectPolicyMutation,
  useCompilePolicyMutation,
  useGetWarmupStatusQuery,
} = api;
