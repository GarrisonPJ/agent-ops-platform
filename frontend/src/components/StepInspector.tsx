import { Code, Eye, TextAlignLeft } from "@phosphor-icons/react";
import { JsonView, allExpanded } from "react-json-view-lite";
import "react-json-view-lite/dist/index.css";

import type { Step } from "../types";

const proMaxJsonStyles = {
  container: "text-[12px] font-mono leading-loose",
  basicChildStyle: "ml-4",
  label: "text-fg-muted mr-1.5 font-medium tracking-wide",
  nullValue: "text-fg-subtle italic",
  undefinedValue: "text-fg-subtle italic",
  stringValue: "text-accent-amber font-medium",
  booleanValue: "text-accent font-medium",
  numberValue: "text-accent font-medium",
  otherValue: "text-fg-primary",
  punctuation: "text-fg-subtle/50",
  collapseIcon: "cursor-pointer text-fg-subtle hover:text-fg-primary mr-1 select-none transition-colors",
  expandIcon: "cursor-pointer text-fg-subtle hover:text-fg-primary mr-1 select-none transition-colors",
  collapsedContent: "text-fg-subtle italic ml-2 cursor-pointer hover:text-fg-muted transition-colors",
};

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
              <div className="px-4 py-2 border-b border-border bg-bg-card flex items-center gap-2">
                <Code className="w-3.5 h-3.5 text-accent" />
                <span className="text-[10px] uppercase font-mono text-fg-muted tracking-[0.05em]">Arguments</span>
              </div>
              <div className="p-4 overflow-x-auto bg-white/[0.01] schema-scroll">
                <JsonView data={step.action.arguments} shouldExpandNode={allExpanded} style={proMaxJsonStyles} />
              </div>
            </div>
          )}

          {step.observation && (
            <div className="flex flex-col flex-1">
              <div className="px-4 py-2 border-b border-border bg-bg-card flex items-center gap-2">
                <Eye className="w-3.5 h-3.5 text-accent" />
                <span className="text-[10px] uppercase font-mono text-fg-muted tracking-[0.05em]">Observation</span>
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
              <div className="px-4 py-2 border-b border-border bg-bg-card flex items-center gap-2">
                <TextAlignLeft className="w-3.5 h-3.5 text-accent" />
                <span className="text-[10px] uppercase font-mono text-fg-muted tracking-[0.05em]">Result Details</span>
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
          <TextAlignLeft className="w-8 h-8 opacity-20 mb-3" />
          <p className="text-sm font-medium">No Step Selected</p>
          <p className="text-xs mt-1 opacity-70">Click a node in the stream to inspect</p>
        </div>
      )}
    </div>
  );
}
