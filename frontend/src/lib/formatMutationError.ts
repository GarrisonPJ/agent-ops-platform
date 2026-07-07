export function formatMutationError(err: unknown): string {
  if (err instanceof Error) return err.message;
  if (err !== null && typeof err === "object" && "status" in err) {
    const rtkErr = err as { status: unknown; error?: unknown };
    if (
      rtkErr.status === "FETCH_ERROR" ||
      rtkErr.status === "PARSING_ERROR" ||
      rtkErr.status === "TIMEOUT_ERROR"
    ) {
      return typeof rtkErr.error === "string"
        ? rtkErr.error
        : String(rtkErr.error ?? "Network request failed");
    }
    if ("message" in rtkErr && typeof rtkErr.message === "string") {
      return rtkErr.message;
    }
  }
  if (err !== null && typeof err === "object" && "data" in err) {
    const data = (err as { data: unknown }).data;
    if (typeof data === "string") return data;
    try {
      return JSON.stringify(data);
    } catch {
      return String(data);
    }
  }
  if (typeof err === "string") return err;
  return "An unexpected error occurred";
}
