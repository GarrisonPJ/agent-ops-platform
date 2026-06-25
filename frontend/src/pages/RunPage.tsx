import { useState, useEffect, useCallback, useRef } from "react";
import { AnimatePresence } from "framer-motion";
import { useNavigate } from "react-router-dom";
import {
  Zap,
  Square,
  ArrowRight,
  Loader2,
  Terminal,
  Code2,
  List
} from "lucide-react";
import { Group, Panel, Separator } from "react-resizable-panels";

import { useAgentStream } from "../hooks/useAgentStream";
import { useRunAgentMutation } from "../services/api";
import StepCard from "../components/StepCard";
import { cn, formatTime } from "../lib/utils";
import StepInspector from "../components/StepInspector";

export default function RunPage() {
  const navigate = useNavigate();
  const [task, setTask] = useState("");
  const [executing, setExecuting] = useState(false);
  const [trajectoryId, setTrajectoryId] = useState<string | null>(null);
  const [submitError, setSubmitError] = useState<string | null>(null);
  const { steps, done, error: streamError } = useAgentStream(executing ? trajectoryId : null);
  const displayError = streamError || submitError;
  const [elapsed, setElapsed] = useState(0);
  const [runAgent, { isLoading: isStarting }] = useRunAgentMutation();

  const [selectedStepIndex, setSelectedStepIndex] = useState<number | null>(null);
  const streamEndRef = useRef<HTMLDivElement>(null);

  // Auto-scroll stream
  useEffect(() => {
    if (executing) {
      streamEndRef.current?.scrollIntoView({ behavior: "smooth" });
      // Auto-select latest step if none selected manually during run
      if (steps.length > 0) {
        setSelectedStepIndex(steps[steps.length - 1].index);
      }
    }
  }, [steps, executing]);

  // Elapsed timer
  useEffect(() => {
    if (!executing) return;
    const start = Date.now();
    setElapsed(0);
    const interval = setInterval(() => {
      setElapsed(Math.floor((Date.now() - start) / 1000));
    }, 1000);
    return () => clearInterval(interval);
  }, [executing]);

  // Stop executing when stream completes or errors
  useEffect(() => {
    if (done || streamError) {
      setExecuting(false);
    }
  }, [done, streamError]);

  const handleSubmit = useCallback(async () => {
    if (!task.trim() || isStarting || executing) return;

    setSubmitError(null);
    setSelectedStepIndex(null);
    setExecuting(true);

    try {
      const response = await runAgent({ task: task.trim() }).unwrap();
      setTrajectoryId(response.trajectory_id);
    } catch {
      setSubmitError("Failed to start task");
      setExecuting(false);
    }
  }, [task, isStarting, executing, runAgent]);

  const handleCancel = useCallback(() => {
    if (trajectoryId) {
      fetch(`/api/agents/${trajectoryId}/cancel`, { method: "POST" }).catch(() => {});
    }
    setExecuting(false);
    setTrajectoryId(null);
    setSelectedStepIndex(null);
  }, [trajectoryId]);

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      handleSubmit();
    }
  };

  const selectedStep = steps.find(s => s.index === selectedStepIndex) ?? null;

  return (
    <div className="h-full w-full flex bg-bg-root">
      <Group>
        {/* ── 1. Input & Status Panel ───────────────────────── */}
        <Panel defaultSize={25} minSize={20} className="flex flex-col border-r border-border bg-bg-root z-10 shadow-xl">
          <div className="flex-1 flex flex-col p-6 overflow-y-auto">
            <h2 className="text-sm font-semibold tracking-wide text-fg-primary mb-6 flex items-center gap-2">
              <Terminal className="w-4 h-4 text-accent" strokeWidth={2} />
              COMMAND CENTER
            </h2>
            
            <div className="flex flex-col gap-3 flex-1">
              <textarea
                value={task}
                onChange={(e) => setTask(e.target.value)}
                onKeyDown={handleKeyDown}
                placeholder="Analyze production logs and isolate the latency spike..."
                disabled={executing}
                className="w-full h-32 bg-bg-card border border-border rounded-md px-4 py-3 text-sm text-fg-primary placeholder-fg-muted/40 outline-none focus:border-accent/50 focus:ring-1 focus:ring-accent/50 transition-all resize-none disabled:opacity-50 shadow-inner-glow"
              />
              
              <button
                onClick={executing ? handleCancel : handleSubmit}
                disabled={!task.trim() || isStarting}
                className={cn(
                  "group w-full font-medium rounded-full px-5 py-3 text-sm active:scale-[0.98] transition-all duration-700 ease-spring flex items-center justify-center gap-2",
                  executing
                    ? "bg-error/8 text-error border border-error/15 hover:bg-error/12"
                    : "bg-fg-primary text-bg-root hover:shadow-card-raised"
                )}
              >
                {isStarting ? (
                  <Loader2 className="w-4 h-4 animate-spin" />
                ) : executing ? (
                  <Square className="w-3.5 h-3.5 fill-current" />
                ) : (
                  <Zap className="w-4 h-4" />
                )}
                {executing ? "Stop Execution" : "Execute Agent"}
              </button>

              {/* Status Section */}
              <div className="mt-8 border-t border-border pt-6">
                <div className="flex items-center justify-between mb-4">
                  <span className="text-xs font-mono text-fg-muted uppercase">Status</span>
                  <span className={cn(
                    "text-xs font-mono font-medium px-2 py-0.5 rounded-md",
                    executing ? "bg-info/10 text-info border border-info/20 animate-pulse" :
                    done && !displayError ? "bg-success/10 text-success border border-success/20" :
                    displayError ? "bg-error/10 text-error border border-error/20" :
                    "bg-white/[0.04] text-fg-subtle border border-border"
                  )}>
                    {executing ? "RUNNING" : done && !displayError ? "COMPLETED" : displayError ? "FAILED" : "IDLE"}
                  </span>
                </div>
                <div className="flex items-center justify-between">
                  <span className="text-xs font-mono text-fg-muted uppercase">Duration</span>
                  <span className="text-sm font-mono text-fg-primary tabular-nums">
                    {formatTime(elapsed)}
                  </span>
                </div>
                {done && !displayError && trajectoryId && (
                  <button
                    onClick={() => navigate(`/traces/${trajectoryId}`)}
                    className="mt-6 w-full flex items-center justify-center gap-1.5 text-xs text-accent bg-accent/10 hover:bg-accent/20 border border-accent/20 rounded-md py-2 font-medium transition-colors"
                  >
                    View Full Trace
                    <ArrowRight className="w-3.5 h-3.5" />
                  </button>
                )}
              </div>
            </div>
          </div>
        </Panel>

        <Separator className="w-[1px] bg-border hover:bg-accent/50 hover:w-[2px] -ml-[1px] transition-all cursor-col-resize z-20" />

        {/* ── 2. Stream / Timeline Panel ─────────────────────── */}
        <Panel defaultSize={35} minSize={30} className="flex flex-col bg-bg-card relative">
          <div className="flex items-center px-6 h-12 border-b border-border shrink-0 bg-bg-card/80 backdrop-blur-md sticky top-0 z-10">
            <h2 className="text-xs font-mono tracking-wider text-fg-muted uppercase flex items-center gap-2">
              <List className="w-3.5 h-3.5" />
              Execution Stream
            </h2>
          </div>
          
          <div className="flex-1 overflow-y-auto px-6 py-6 scroll-smooth">
            {!executing && steps.length === 0 && !done && (
              <div className="h-full flex flex-col items-center justify-center text-fg-subtle">
                <div className="w-12 h-12 rounded-full border border-dashed border-border flex items-center justify-center mb-4">
                  <Zap className="w-5 h-5 opacity-50" />
                </div>
                <p className="text-sm font-medium">No active execution</p>
                <p className="text-xs mt-1">Submit a command to begin</p>
              </div>
            )}

            <AnimatePresence>
              {steps.map((step) => (
                <StepCard
                  key={step.index}
                  step={step}
                  isResult={step.action === null && done}
                  isSelected={selectedStepIndex === step.index}
                  onClick={() => setSelectedStepIndex(step.index)}
                />
              ))}
            </AnimatePresence>

            {executing && (
              <div className="flex items-center gap-3 py-4 pl-8 opacity-60">
                <Loader2 className="w-4 h-4 animate-spin text-accent" />
                <span className="text-xs font-mono text-accent">Agent is thinking...</span>
              </div>
            )}
            
            <div ref={streamEndRef} className="h-4" />
          </div>
        </Panel>

        <Separator className="w-[1px] bg-border hover:bg-accent/50 hover:w-[2px] -ml-[1px] transition-all cursor-col-resize z-20" />

        {/* ── 3. Inspector Panel ───────────────────────────── */}
        <Panel defaultSize={40} minSize={25} className="flex flex-col bg-bg-root">
          <div className="flex items-center px-4 h-12 border-b border-border shrink-0 bg-bg-root">
            <h2 className="text-xs font-mono tracking-wider text-fg-muted uppercase flex items-center gap-2">
              <Code2 className="w-3.5 h-3.5" />
              Inspector
            </h2>
          </div>
          
          <StepInspector step={selectedStep} />
        </Panel>
      </Group>
    </div>
  );
}
