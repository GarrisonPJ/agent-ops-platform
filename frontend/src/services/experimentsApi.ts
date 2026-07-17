import { createApi, fetchBaseQuery } from "@reduxjs/toolkit/query/react";
import { z } from "zod";
import {
  experimentSchema,
  policySchema,
  runAnalysisSchema,
  runSchema,
} from "../schemas/phase1";
import type {
  CreateExperimentRequest,
  Experiment,
  Policy,
  Run,
  RunAnalysis,
} from "../types/phase1";
import { recordedBaseQuery } from "./recorded/handlers";


const IS_RECORDED_DEMO = import.meta.env.VITE_MOCK_API === "true";
const experimentListSchema = z.array(experimentSchema);

export const experimentsApi = createApi({
  reducerPath: "experimentsApi",
  baseQuery: IS_RECORDED_DEMO
    ? recordedBaseQuery
    : fetchBaseQuery({ baseUrl: "/api" }),
  tagTypes: ["Experiments", "Experiment", "Run", "Analysis"],
  endpoints: (builder) => ({
    getExperiments: builder.query<Experiment[], void>({
      query: () => "/experiments",
      transformResponse: (response) => experimentListSchema.parse(response),
      providesTags: (result) => [
        { type: "Experiments", id: "LIST" },
        ...(result ?? []).map(({ id }) => ({
          type: "Experiment" as const,
          id,
        })),
      ],
    }),
    createExperiment: builder.mutation<
      Experiment,
      CreateExperimentRequest
    >({
      query: (body) => ({ url: "/experiments", method: "POST", body }),
      transformResponse: (response) => experimentSchema.parse(response),
      invalidatesTags: [{ type: "Experiments", id: "LIST" }],
    }),
    getExperiment: builder.query<Experiment, string>({
      query: (id) => `/experiments/${id}`,
      transformResponse: (response) => experimentSchema.parse(response),
      providesTags: (_result, _error, id) => [{ type: "Experiment", id }],
    }),
    createBaselineRun: builder.mutation<Run, string>({
      query: (experimentId) => ({
        url: `/experiments/${experimentId}/runs`,
        method: "POST",
        body: {},
      }),
      transformResponse: (response) => runSchema.parse(response),
      invalidatesTags: (_result, _error, experimentId) => [
        { type: "Experiment", id: experimentId },
        { type: "Experiments", id: "LIST" },
      ],
    }),
    getRun: builder.query<Run, string>({
      query: (id) => `/runs/${id}`,
      transformResponse: (response) => runSchema.parse(response),
      providesTags: (_result, _error, id) => [{ type: "Run", id }],
    }),
    cancelRun: builder.mutation<Run, string>({
      query: (id) => ({ url: `/runs/${id}/cancel`, method: "POST" }),
      transformResponse: (response) => runSchema.parse(response),
      invalidatesTags: (_result, _error, id) => [{ type: "Run", id }],
    }),
    getRunAnalysis: builder.query<RunAnalysis, string>({
      query: (id) => `/runs/${id}/analysis`,
      transformResponse: (response) => runAnalysisSchema.parse(response),
      providesTags: (_result, _error, id) => [{ type: "Analysis", id }],
    }),
    replayPolicy: builder.mutation<Policy, string>({
      query: (id) => ({ url: `/policies/${id}/replay`, method: "POST" }),
      transformResponse: (response) => policySchema.parse(response),
      invalidatesTags: [{ type: "Experiments", id: "LIST" }],
    }),
    activatePolicy: builder.mutation<Policy, string>({
      query: (id) => ({
        url: `/policies/${id}/activate`,
        method: "POST",
      }),
      transformResponse: (response) => policySchema.parse(response),
      invalidatesTags: [{ type: "Experiments", id: "LIST" }],
    }),
    rejectPolicy: builder.mutation<Policy, string>({
      query: (id) => ({ url: `/policies/${id}/reject`, method: "POST" }),
      transformResponse: (response) => policySchema.parse(response),
      invalidatesTags: [{ type: "Experiments", id: "LIST" }],
    }),
  }),
});

export const {
  useGetExperimentsQuery,
  useCreateExperimentMutation,
  useGetExperimentQuery,
  useCreateBaselineRunMutation,
  useGetRunQuery,
  useCancelRunMutation,
  useGetRunAnalysisQuery,
  useReplayPolicyMutation,
  useActivatePolicyMutation,
  useRejectPolicyMutation,
} = experimentsApi;
