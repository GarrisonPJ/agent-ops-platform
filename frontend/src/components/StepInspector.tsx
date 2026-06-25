import { AlignLeft } from "lucide-react";
import { JsonView, allExpanded, darkStyles } from "react-json-view-lite";
import "react-json-view-lite/dist/index.css";

import type { Step } from "../types";

interface StepInspectorProps {
  step: Step | null;
}

export default function StepInspector({ step }: StepInspectorProps) {
  return (
    <div className="flex-1 overflow-y-auto p-0">
      {step ? (
        <div className="flex flex-col h-full">
          {step.action && (
            <div className="flex flex-col border-b border-border">
              <div className="px-4 py-2 border-b border-border bg-white/[0.02]">
                <span className="text-[10px] uppercase font-mono text-fg-muted tracking-wider">Arguments</span>
              </div>
              <div className="p-4 overflow-x-auto bg-[#050505]">
                <JsonView data={step.action.arguments} shouldExpandNode={allExpanded} style={darkStyles} />
              </div>
            </div>
          )}

          {step.observation && (
            <div className="flex flex-col flex-1">
              <div className="px-4 py-2 border-b border-border bg-white/[0.02]">
                <span className="text-[10px] uppercase font-mono text-fg-muted tracking-wider">Observation</span>
              </div>
              <div className="p-4 overflow-x-auto flex-1 bg-bg-root">
                <pre className="text-[12px] font-mono leading-relaxed text-fg-muted whitespace-pre-wrap">
                  {step.observation}
                </pre>
              </div>
            </div>
          )}

          {!step.action && !step.observation && (
            <div className="flex flex-col flex-1">
              <div className="px-4 py-2 border-b border-border bg-white/[0.02]">
                <span className="text-[10px] uppercase font-mono text-fg-muted tracking-wider">Result Details</span>
              </div>
              <div className="p-4 overflow-x-auto flex-1 bg-bg-root">
                <p className="text-[13px] text-fg-primary leading-relaxed">
                  {step.thought}
                </p>
              </div>
            </div>
          )}
        </div>
      ) : (
        <div className="h-full flex flex-col items-center justify-center text-fg-subtle">
          <AlignLeft className="w-8 h-8 opacity-20 mb-3" />
          <p className="text-sm font-medium">No Step Selected</p>
          <p className="text-xs mt-1 opacity-70">Click a node in the stream to inspect</p>
        </div>
      )}
    </div>
  );
}
