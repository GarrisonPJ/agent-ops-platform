import { useEffect, useRef } from "react";
import { motion, AnimatePresence } from "framer-motion";
import {
  Play,
  Pause,
  SkipBack,
  SkipForward,
  Clock,
  List,
} from "lucide-react";
import StepCard from "./StepCard";
import TokenBadge from "./TokenBadge";
import ContextWindowBar from "./ContextWindowBar";
import { cn } from "../lib/utils";
import type { Step } from "../types";

export interface ExecutionTimelineProps {
  steps: Step[];
  currentStepIndex: number;
  isPlaying: boolean;
  hasEverPlayed: boolean;
  progress: number;
  onSeek: (stepIndex: number) => void;
  onProgressClick: (e: React.MouseEvent<HTMLDivElement>) => void;
  onTogglePlay: () => void;
  onCycleSpeed: () => void;
  playbackSpeed: number;
}

export default function ExecutionTimeline({
  steps,
  currentStepIndex,
  isPlaying,
  hasEverPlayed,
  progress,
  onSeek,
  onProgressClick,
  onTogglePlay,
  onCycleSpeed,
  playbackSpeed,
}: ExecutionTimelineProps) {
  const streamEndRef = useRef<HTMLDivElement>(null);
  const currentStep = steps[currentStepIndex] ?? null;

  // Auto-scroll stream during playback
  useEffect(() => {
    if (isPlaying) {
      streamEndRef.current?.scrollIntoView({
        behavior: "smooth",
        block: "nearest",
      });
    }
  }, [currentStepIndex, isPlaying]);

  return (
    <div className="flex flex-col bg-bg-card relative h-full">
      <div className="flex items-center px-6 h-10 border-b border-border shrink-0 bg-bg-card/80 backdrop-blur-md sticky top-0 z-10">
        <h2 className="text-[10px] font-mono tracking-wider text-fg-muted uppercase flex items-center gap-2">
          <List className="w-3.5 h-3.5" />
          Execution Playback
        </h2>
      </div>

      <div className="flex-1 overflow-y-auto px-6 py-6 scroll-smooth">
        <AnimatePresence mode="popLayout">
          {steps.map((step, idx) => {
            // Only show up to current step during playback
            if (hasEverPlayed && idx > currentStepIndex) return null;

            return (
              <motion.div
                key={step.index}
                initial={{ opacity: 0, y: 10 }}
                animate={{ opacity: 1, y: 0 }}
                exit={{ opacity: 0, scale: 0.95 }}
                transition={{ duration: 0.2 }}
              >
                <StepCard
                  step={step}
                  isResult={step.action === null}
                  isSelected={currentStepIndex === step.index}
                  isPlaying={isPlaying}
                  onClick={() => {
                    if (isPlaying) {
                      onTogglePlay();
                    }
                    onSeek(step.index);
                  }}
                  footer={
                    <div className="flex items-center justify-between w-full mt-2 border-t border-border pt-2 opacity-80">
                      <TokenBadge
                        prompt={step.token_prompt}
                        completion={step.token_completion}
                      />
                      {step.context_window.limit > 0 && (
                        <span className="inline-flex items-center gap-1.5">
                          <span className="text-[9px] text-fg-muted font-mono uppercase">
                            Ctx
                          </span>
                          <ContextWindowBar
                            used={step.context_window.used}
                            limit={step.context_window.limit}
                          />
                        </span>
                      )}
                    </div>
                  }
                />
              </motion.div>
            );
          })}
        </AnimatePresence>
        <div ref={streamEndRef} className="h-4" />
      </div>

      {/* Playback Controls (Sticky Bottom) */}
      <div className="border-t border-border bg-bg-card shrink-0">
        {/* Mini overview dots */}
        {steps.length > 0 && (
          <div className="px-6 pt-3 pb-1 flex items-center gap-[3px] justify-center">
            {steps.map((step, idx) => (
              <button
                key={step.index}
                onMouseEnter={() => onSeek(idx)}
                className={cn(
                  "w-[6px] h-[6px] rounded-full transition-all duration-300 shrink-0 cursor-pointer",
                  step.action !== null ? "bg-accent" : "bg-accent-amber",
                  idx === currentStepIndex && isPlaying && "mini-dot-current",
                  idx !== currentStepIndex && "opacity-40 hover:opacity-80",
                )}
                title={`Step ${idx + 1}`}
              />
            ))}
          </div>
        )}
        {/* Progress Bar */}
        <div
          className={cn(
            "progress-bar-container relative h-1 bg-border cursor-pointer group hover:h-2 transition-all",
            isPlaying && "is-playing",
          )}
          onClick={onProgressClick}
        >
          <div
            className={cn(
              "h-full bg-accent relative",
              !isPlaying && "transition-all duration-300 ease-linear",
            )}
            style={{ width: `${progress}%` }}
          >
            <div className="progress-glow-dot" />
          </div>
        </div>
        <div className="px-6 py-3 flex items-center justify-between">
          <div className="flex items-center gap-3">
            <span className="text-[11px] text-fg-muted font-mono tabular-nums">
              Step {Math.min(currentStepIndex + 1, steps.length)} /{" "}
              {steps.length}
            </span>
            {currentStep && (
              <span className="text-[11px] text-fg-subtle font-mono flex items-center gap-1">
                <Clock className="w-3 h-3" />
                {(currentStep.latency_ms / 1000).toFixed(1)}s
              </span>
            )}
          </div>
          <div className="flex items-center gap-1">
            <button
              onClick={() => onSeek(currentStepIndex - 1)}
              disabled={currentStepIndex === 0}
              className="w-8 h-8 flex items-center justify-center rounded-md text-fg-muted hover:bg-white/[0.04] active:scale-[0.95] disabled:opacity-30 transition-all"
            >
              <SkipBack className="w-4 h-4" />
            </button>
            <button
              onClick={onTogglePlay}
              className="w-10 h-10 flex items-center justify-center rounded-md text-accent bg-accent/10 hover:bg-accent/20 active:scale-[0.95] shadow-inner-glow transition-all"
            >
              {isPlaying ? (
                <Pause className="w-5 h-5" />
              ) : (
                <Play className="w-5 h-5" />
              )}
            </button>
            <button
              onClick={() => onSeek(currentStepIndex + 1)}
              disabled={currentStepIndex >= steps.length - 1}
              className="w-8 h-8 flex items-center justify-center rounded-md text-fg-muted hover:bg-white/[0.04] active:scale-[0.95] disabled:opacity-30 transition-all"
            >
              <SkipForward className="w-4 h-4" />
            </button>
            <button
              onClick={onCycleSpeed}
              className="w-8 h-8 ml-2 flex items-center justify-center rounded-md text-fg-subtle hover:bg-white/[0.04] hover:text-fg-primary font-mono text-[10px] tabular-nums transition-all"
            >
              {playbackSpeed}x
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
