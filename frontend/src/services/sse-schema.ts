import { z } from "zod";

/** Zod schema for Step, used to validate SSE payloads at the parse boundary. */
export const StepSchema = z.object({
  index: z.number(),
  thought: z.string(),
  action: z
    .object({
      id: z.string(),
      name: z.string(),
      arguments: z.record(z.string(), z.unknown()),
    })
    .nullable(),
  observation: z.string(),
  latency_ms: z.number(),
  started_at: z
    .union([z.number(), z.string()])
    .transform((val) =>
      typeof val === "number"
        ? new Date(val > 1e12 ? val : val * 1000).toISOString()
        : val,
    ),
  context_window: z.object({
    used: z.number(),
    limit: z.number(),
  }),
  token_prompt: z.number().nullable(),
  token_completion: z.number().nullable(),
});

export type ValidatedStep = z.infer<typeof StepSchema>;
