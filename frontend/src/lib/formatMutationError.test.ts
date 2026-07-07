import { describe, it, expect } from "vitest";
import { formatMutationError } from "./formatMutationError";

describe("formatMutationError", () => {
  it("extracts message from Error instances", () => {
    expect(formatMutationError(new Error("msg"))).toBe("msg");
  });

  it("extracts data from RTK-style error objects", () => {
    expect(formatMutationError({ data: "err" })).toBe("err");
  });

  it("JSON.stringifies object data from RTK-style errors", () => {
    expect(formatMutationError({ data: { detail: "server error" } })).toBe(
      '{"detail":"server error"}',
    );
  });

  it("returns raw strings directly", () => {
    expect(formatMutationError("raw string")).toBe("raw string");
  });

  it("returns fallback for null", () => {
    expect(formatMutationError(null)).toBe("An unexpected error occurred");
  });

  it("returns fallback for undefined", () => {
    expect(formatMutationError(undefined)).toBe("An unexpected error occurred");
  });

  it("returns fallback for numbers", () => {
    expect(formatMutationError(42)).toBe("An unexpected error occurred");
  });

  it("returns fallback for objects without data property", () => {
    expect(formatMutationError({ foo: "bar" })).toBe(
      "An unexpected error occurred",
    );
  });

  describe("RTK Query error statuses", () => {
    it("extracts error from FETCH_ERROR", () => {
      expect(
        formatMutationError({ status: "FETCH_ERROR", error: "Network failure" }),
      ).toBe("Network failure");
    });

    it("extracts error from PARSING_ERROR", () => {
      expect(
        formatMutationError({
          status: "PARSING_ERROR",
          error: "Unexpected token",
        }),
      ).toBe("Unexpected token");
    });

    it("extracts error from TIMEOUT_ERROR", () => {
      expect(
        formatMutationError({ status: "TIMEOUT_ERROR", error: "Timed out" }),
      ).toBe("Timed out");
    });

    it("falls back to generic message when status error is missing", () => {
      expect(formatMutationError({ status: "FETCH_ERROR" })).toBe(
        "Network request failed",
      );
    });

    it("coerces non-string error to string", () => {
      expect(
        formatMutationError({ status: "FETCH_ERROR", error: 500 }),
      ).toBe("500");
    });

    it("returns message field for other status codes", () => {
      expect(
        formatMutationError({ status: 400, message: "Bad request" }),
      ).toBe("Bad request");
    });
  });

  describe("JSON.stringify safety", () => {
    it("handles circular references gracefully", () => {
      const obj: Record<string, unknown> = { a: 1 };
      obj.self = obj;
      // Should not throw; returns String(data) which yields "[object Object]"
      expect(formatMutationError({ data: obj })).toBe("[object Object]");
    });
  });
});
