import { createApi, fetchBaseQuery } from "@reduxjs/toolkit/query/react";
import type {
  TrajectorySummary,
  TrajectoryDetail,
  RunResponse,
  ToolInfo,
  CompareResponse,
  BenchmarkTask,
  BenchmarkResponse,
} from "../types";

export const api = createApi({
  reducerPath: "api",
  baseQuery: fetchBaseQuery({ baseUrl: "/api" }),
  tagTypes: ["Traces", "Trace", "Tools"],
  endpoints: (builder) => ({
    runAgent: builder.mutation<RunResponse, { task: string }>({
      query: (body) => ({
        url: "/agents/run",
        method: "POST",
        body,
      }),
      invalidatesTags: [{ type: 'Traces' as const }],
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
      invalidatesTags: [{ type: 'Traces' as const }],
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
} = api;
