import { describe, it, expect, vi, beforeEach } from "vitest";
import { renderHook, waitFor } from "@testing-library/react";
import { usePolicyActions } from "./usePolicyActions";

// --- Mocks (vi.hoisted ensures mock factory can reference these) ---

const [mockApproveTrigger, mockRejectTrigger, mockLoadingState, toastMock] =
  vi.hoisted(() => {
    return [
      vi.fn(),
      vi.fn(),
      { approve: false },
      { success: vi.fn(), error: vi.fn() },
    ];
  });

vi.mock("../services/api", () => ({
  useApprovePolicyMutation: vi.fn(() => {
    return [mockApproveTrigger, { isLoading: mockLoadingState.approve }];
  }),
  useRejectPolicyMutation: vi.fn(() => {
    return [mockRejectTrigger, { isLoading: false }];
  }),
}));

vi.mock("../lib/toast", () => ({ toast: toastMock }));

// --- Helpers ---

function resolveUnwrap<T>(value: T) {
  return { unwrap: () => Promise.resolve(value) };
}

function rejectUnwrap(error: Error) {
  return { unwrap: () => Promise.reject(error) };
}

// --- Suite ---

describe("usePolicyActions", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockLoadingState.approve = false;
  });

  it("approvePolicy calls the approve mutation and shows success toast on resolve", async () => {
    mockApproveTrigger.mockReturnValue(resolveUnwrap({}));
    const { result } = renderHook(() => usePolicyActions());

    await result.current.approvePolicy("ver-123");

    expect(mockApproveTrigger).toHaveBeenCalledTimes(1);
    expect(mockApproveTrigger).toHaveBeenCalledWith("ver-123");
    expect(toastMock.success).toHaveBeenCalledWith(
      "Policy approved and activated",
    );
    expect(toastMock.error).not.toHaveBeenCalled();
  });

  it("approvePolicy shows error toast on mutation rejection", async () => {
    mockApproveTrigger.mockReturnValue(
      rejectUnwrap(new Error("server error")),
    );
    const { result } = renderHook(() => usePolicyActions());

    await result.current.approvePolicy("ver-123");

    expect(toastMock.error).toHaveBeenCalledWith("Failed to approve policy");
    expect(toastMock.success).not.toHaveBeenCalled();
  });

  it("rejectWithReason calls window.prompt then rejects with the given reason", async () => {
    const promptSpy = vi.spyOn(window, "prompt").mockReturnValue("bad config");
    mockRejectTrigger.mockReturnValue(resolveUnwrap({}));
    const { result } = renderHook(() => usePolicyActions());

    await result.current.rejectWithReason("ver-456");

    expect(promptSpy).toHaveBeenCalledWith(
      "Reason for rejection (optional):",
    );
    expect(mockRejectTrigger).toHaveBeenCalledWith({
      version_id: "ver-456",
      reason: "bad config",
    });
    expect(toastMock.success).toHaveBeenCalledWith("Policy rejected");
    promptSpy.mockRestore();
  });

  it("rejectWithReason returns early when prompt is cancelled (null)", async () => {
    const promptSpy = vi.spyOn(window, "prompt").mockReturnValue(null);
    const { result } = renderHook(() => usePolicyActions());

    await result.current.rejectWithReason("ver-456");

    expect(mockRejectTrigger).not.toHaveBeenCalled();
    expect(toastMock.success).not.toHaveBeenCalled();
    expect(toastMock.error).not.toHaveBeenCalled();
    promptSpy.mockRestore();
  });

  it("rejectPolicy calls the reject mutation with explicit version and reason", async () => {
    mockRejectTrigger.mockReturnValue(resolveUnwrap({}));
    const { result } = renderHook(() => usePolicyActions());

    await result.current.rejectPolicy("ver-789", "violates constraints");

    expect(mockRejectTrigger).toHaveBeenCalledWith({
      version_id: "ver-789",
      reason: "violates constraints",
    });
    expect(toastMock.success).toHaveBeenCalledWith("Policy rejected");
  });

  it("rejectPolicy shows error toast on mutation rejection", async () => {
    mockRejectTrigger.mockReturnValue(
      rejectUnwrap(new Error("service unavailable")),
    );
    const { result } = renderHook(() => usePolicyActions());

    await result.current.rejectPolicy("ver-789", "bad");

    expect(toastMock.error).toHaveBeenCalledWith("Failed to reject policy");
    expect(toastMock.success).not.toHaveBeenCalled();
  });

  it("isApproving reflects the loading state from useApprovePolicyMutation", () => {
    mockLoadingState.approve = false;
    const { result, rerender } = renderHook(() => usePolicyActions());
    expect(result.current.isApproving).toBe(false);

    mockLoadingState.approve = true;
    rerender();
    expect(result.current.isApproving).toBe(true);
  });

  it("rejectingId is set during rejection and cleared after completion", async () => {
    let resolveDeferred!: (val: unknown) => void;
    const deferredPromise = new Promise((resolve) => {
      resolveDeferred = resolve;
    });
    mockRejectTrigger.mockReturnValue({ unwrap: () => deferredPromise });

    const { result } = renderHook(() => usePolicyActions());

    // Start rejection — rejectingId should be set after React flushes the batch
    const promise = result.current.rejectPolicy("ver-999", "testing");

    // waitFor retries inside act() so the synchronous setRejectingId gets flushed
    await waitFor(() => {
      expect(result.current.rejectingId).toBe("ver-999");
    });

    // Let the mutation resolve
    resolveDeferred({});
    await waitFor(() => expect(result.current.rejectingId).toBeNull());
    await promise;
  });
});
