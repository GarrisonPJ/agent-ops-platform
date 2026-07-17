import type { Run } from "../types/phase1";

export function formatDate(value: string | null) {
  if (!value) return "—";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "—";
  return new Intl.DateTimeFormat("en", {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  }).format(date);
}

export function formatScore(score: number | null) {
  return score == null ? "—" : score.toFixed(2);
}

export function formatDuration(milliseconds: number | null | undefined) {
  if (milliseconds == null) return "—";
  return milliseconds >= 1000
    ? `${(milliseconds / 1000).toFixed(1)}s`
    : `${milliseconds}ms`;
}

export function totalTokens(run: Run) {
  if (!run.metrics) return null;
  if (typeof run.metrics.total_tokens === "number") return run.metrics.total_tokens;
  const prompt = typeof run.metrics.token_prompt === "number" ? run.metrics.token_prompt : 0;
  const completion =
    typeof run.metrics.token_completion === "number" ? run.metrics.token_completion : 0;
  return prompt + completion || null;
}

export function getApiErrorMessage(error: unknown) {
  if (error instanceof Error) return error.message;
  if (!error || typeof error !== "object") return "An unexpected error occurred.";
  if ("data" in error) {
    const data = (error as { data?: unknown }).data;
    if (typeof data === "string") return data;
    if (data && typeof data === "object" && "message" in data) {
      const message = (data as { message?: unknown }).message;
      if (typeof message === "string") return message;
    }
  }
  if ("error" in error && typeof (error as { error?: unknown }).error === "string") {
    return (error as { error: string }).error;
  }
  return "The request could not be completed.";
}
