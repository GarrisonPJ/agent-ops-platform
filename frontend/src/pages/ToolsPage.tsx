import { useState } from "react";
import { Wrench, CaretDown } from "@phosphor-icons/react";
import { motion, AnimatePresence } from "framer-motion";
import { JsonView, allExpanded } from "react-json-view-lite";
import "react-json-view-lite/dist/index.css";
import { useGetToolsQuery, useToggleToolMutation } from "../services/api";
import type { ToolInfo } from "../types";
import EmptyState from "../components/EmptyState";
import LoadingSkeleton from "../components/LoadingSkeleton";

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

function ToolCard({ tool }: { tool: ToolInfo }) {
  const [expanded, setExpanded] = useState(false);
  const [toggleTool] = useToggleToolMutation();
  const enabled = tool.enabled;

  return (
    <div
      className={`bg-bg-card border shadow-inner-glow rounded-xl p-5 flex flex-col transition-colors duration-150 ease-out ${
        enabled
          ? "border-border/60 hover:bg-white/[0.02] hover:border-border"
          : "border-white/[0.03]"
      }`}
    >
      <div className="flex justify-between items-start">
        <div className="flex items-center gap-3 min-w-0 flex-1">
          <div className={`w-8 h-8 rounded-md bg-accent/10 border border-accent/20 flex items-center justify-center shrink-0 ${enabled ? "text-accent" : "text-fg-subtle"}`}>
            <Wrench className={`w-4 h-4 ${enabled ? "" : "opacity-40"}`} weight="regular" />
          </div>
          <span className={`font-mono text-sm font-semibold truncate pr-2 ${enabled ? "text-fg-primary" : "text-fg-subtle"}`}>{tool.name}</span>
        </div>
        {/* Toggle switch — calls API */}
        <button
          onClick={(e) => {
            e.stopPropagation();
            toggleTool(tool.name);
          }}
          role="switch"
          aria-checked={enabled}
          aria-label={enabled ? "Disable tool" : "Enable tool"}
          className={`relative w-9 h-5 rounded-full transition-colors duration-300 flex-shrink-0 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent focus-visible:ring-offset-2 focus-visible:ring-offset-bg-card ${
            enabled ? "bg-accent" : "bg-white/[0.08]"
          }`}
          title={enabled ? "Disable tool" : "Enable tool"}
        >
          <span
            className={`absolute top-[2px] w-4 h-4 bg-white rounded-full shadow-sm transition-[left] duration-300 ${
              enabled ? "left-[18px]" : "left-[2px]"
            }`}
          />
        </button>
      </div>

      <p className={`text-[13px] leading-relaxed mt-3 ${enabled ? "text-fg-muted" : "text-fg-subtle"}`}>{tool.description}</p>

      {/* Expand chevron */}
      <button
        onClick={(e) => {
          e.stopPropagation();
          setExpanded((v) => !v);
        }}
        className={`flex items-center gap-1.5 mt-3 text-xs transition-colors self-start rounded-sm focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-accent ${enabled ? "text-fg-muted hover:text-fg-primary" : "text-fg-subtle"}`}
      >
        <CaretDown
          className={`w-3.5 h-3.5 transition-transform duration-200 ${expanded ? "rotate-180" : ""}`}
        />
        {expanded ? "Hide schema" : "View schema"}
      </button>

      {/* Expandable schema — inline flow, only pushes in its own column */}
      <AnimatePresence>
        {expanded && (
          <motion.div
            initial={{ height: 0, opacity: 0 }}
            animate={{ height: "auto", opacity: 1 }}
            exit={{ height: 0, opacity: 0 }}
            transition={{ duration: 0.2, ease: "easeOut" }}
            className="mt-4 bg-white/[0.01] border border-white/[0.03] shadow-inner-glow rounded-lg p-4 overflow-x-auto overflow-hidden schema-scroll"
          >
            <JsonView data={tool.parameters} shouldExpandNode={allExpanded} style={proMaxJsonStyles} />
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  );
}

export default function ToolsPage() {
  const { data: tools, isLoading } = useGetToolsQuery();

  return (
    <div className="flex-1 flex flex-col">
      {/* ── Title ──────────────────────────────────── */}
      <div className="max-w-5xl mx-auto px-6 pt-8 pb-6 w-full shrink-0">
        <h1 className="text-2xl font-semibold tracking-tight text-fg-primary">Tools</h1>
        <p className="text-sm text-fg-muted mt-1">Manage active agent capabilities.</p>
      </div>

      {/* ── Content ─────────────────────────────────── */}
      <motion.div
        initial={{ opacity: 0, y: 16 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: 0.6, ease: [0.16, 1, 0.3, 1] }}
        className="flex-1 overflow-auto max-w-5xl mx-auto px-6 pb-8 w-full"
      >
        {/* Loading state */}
        {isLoading ? (
          <LoadingSkeleton variant="cards" />
        ) : tools && tools.length > 0 ? (
          <div className="columns-1 md:columns-3 gap-5 [&>div]:break-inside-avoid [&>div]:mb-5">
            {tools.map((tool) => (
              <ToolCard key={tool.name} tool={tool} />
            ))}
          </div>
        ) : (
          <EmptyState
            icon={Wrench}
            message="No Tools Registered"
            description="Register a tool to give the agent new capabilities."
          />
        )}
      </motion.div>
    </div>
  );
}
