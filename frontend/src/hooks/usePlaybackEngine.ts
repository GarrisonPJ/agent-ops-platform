import { useCallback, useEffect, useRef, useState } from "react";
import { useDispatch, useSelector } from "react-redux";
import { setCurrentStep, togglePlay, setSpeed } from "../store/trajectorySlice";
import type { RootState, AppDispatch } from "../store";
import type { Step, TrajectoryDetail } from "../types";

const STEP_DURATION_MS = 1000;

export function usePlaybackEngine(
  steps: Step[],
  totalPlaybackMs: number,
  trace: TrajectoryDetail | undefined,
) {
  const dispatch = useDispatch<AppDispatch>();
  const { isPlaying, playbackSpeed } = useSelector(
    (state: RootState) => state.trajectory,
  );

  const [elapsedMs, setElapsedMs] = useState(0);
  const [hasEverPlayed, setHasEverPlayed] = useState(false);
  const rafRef = useRef<number>(0);
  const elapsedMsRef = useRef(0);
  const rafStartTimeRef = useRef(0);
  const lastStepDispatchedRef = useRef(-1);

  // ── Continuous playback via requestAnimationFrame ──────
  useEffect(() => {
    if (!isPlaying || !trace || steps.length === 0) return;

    if (!hasEverPlayed) setHasEverPlayed(true);

    // If playback completed, restart from beginning
    if (elapsedMsRef.current >= totalPlaybackMs) {
      elapsedMsRef.current = 0;
      lastStepDispatchedRef.current = -1;
      dispatch(setCurrentStep(0));
      setElapsedMs(0);
    }

    rafStartTimeRef.current =
      performance.now() - elapsedMsRef.current / playbackSpeed;

    const tick = (now: number) => {
      const elapsed = (now - rafStartTimeRef.current) * playbackSpeed;
      elapsedMsRef.current = elapsed;

      const stepIdx = Math.min(
        Math.floor(elapsed / STEP_DURATION_MS),
        steps.length - 1,
      );

      if (stepIdx !== lastStepDispatchedRef.current) {
        lastStepDispatchedRef.current = stepIdx;
        dispatch(setCurrentStep(stepIdx));
      }

      if (elapsed >= totalPlaybackMs) {
        dispatch(togglePlay());
        return;
      }

      setElapsedMs(elapsed);
      rafRef.current = requestAnimationFrame(tick);
    };

    rafRef.current = requestAnimationFrame(tick);

    return () => {
      cancelAnimationFrame(rafRef.current);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [isPlaying, playbackSpeed, trace, dispatch, steps.length, totalPlaybackMs]);

  const handleManualSeek = useCallback(
    (stepIndex: number) => {
      const idx = Math.max(0, Math.min(stepIndex, steps.length - 1));
      const elapsed = idx * STEP_DURATION_MS;
      elapsedMsRef.current = elapsed;
      rafStartTimeRef.current = performance.now() - elapsed / playbackSpeed;
      lastStepDispatchedRef.current = idx;
      setElapsedMs(elapsed);
      dispatch(setCurrentStep(idx));
    },
    [steps.length, dispatch, playbackSpeed],
  );

  const handleProgressClick = useCallback(
    (e: React.MouseEvent<HTMLDivElement>) => {
      if (steps.length === 0) return;
      const rect = e.currentTarget.getBoundingClientRect();
      const x = e.clientX - rect.left;
      const pct = x / rect.width;
      const idx = Math.min(Math.floor(pct * steps.length), steps.length - 1);
      handleManualSeek(idx);
    },
    [steps.length, handleManualSeek],
  );

  const handleCycleSpeed = useCallback(() => {
    const speedMap: Record<number, 0.5 | 1 | 2 | 4> = {
      0.5: 1,
      1: 2,
      2: 4,
      4: 0.5,
    };
    dispatch(setSpeed(speedMap[playbackSpeed]));
  }, [playbackSpeed, dispatch]);

  return {
    elapsedMs,
    hasEverPlayed,
    handleManualSeek,
    handleProgressClick,
    handleCycleSpeed,
  };
}
