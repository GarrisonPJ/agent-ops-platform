import { useSearchParams } from "react-router-dom";
import {
  Flask,
  Bug,
  Shield,
} from "@phosphor-icons/react";
import BenchmarkTab from "./eval/BenchmarkTab";
import FailuresTab from "./eval/FailuresTab";
import PoliciesTab from "./eval/PoliciesTab";

const TABS: { key: string; label: string; icon: typeof Flask }[] = [
  { key: "benchmark", label: "Benchmark", icon: Flask },
  { key: "failures", label: "Failures", icon: Bug },
  { key: "policies", label: "Policies", icon: Shield },
];

export default function EvalPage() {
  const [searchParams, setSearchParams] = useSearchParams();
  const view = searchParams.get("view") || "benchmark";

  const setView = (v: string) => {
    setSearchParams(v === "benchmark" ? {} : { view: v });
  };

  return (
    <div className="flex-1 flex flex-col">
      {/* ── Header ──────────────────────────────────── */}
      <div className="max-w-5xl mx-auto px-6 pt-8 pb-6 w-full shrink-0">
        <h1 className="text-2xl font-semibold tracking-tight text-fg-primary">Eval</h1>
        <p className="text-sm text-fg-muted mt-1">Evaluation & quality benchmarks.</p>
      </div>

      {/* ── Content ─────────────────────────────────── */}
      <div className="flex-1 overflow-auto max-w-5xl mx-auto px-6 pb-8 w-full space-y-6">
        {/* ── Tab navigation ─────────────────────────────────────── */}
        <div className="flex items-center gap-1 bg-bg-card border border-border rounded-lg p-0.5" role="tablist" aria-label="Evaluation views">
          {TABS.map((tab) => {
            const Icon = tab.icon;
            const active = view === tab.key;
            return (
              <button
                key={tab.key}
                id={`tab-${tab.key}`}
                role="tab"
                aria-selected={active}
                aria-controls={`panel-${tab.key}`}
                onClick={() => setView(tab.key)}
                className={`flex items-center gap-2 px-4 py-1.5 text-xs font-mono rounded-md transition-colors duration-200 ${
                  active
                    ? "bg-accent/15 text-accent border border-accent/30 font-semibold"
                    : "text-fg-muted hover:text-fg-primary"
                }`}
              >
                <Icon className="w-3.5 h-3.5" />
                {tab.label}
              </button>
            );
          })}
        </div>

        {/* ── Tab content (all kept mounted to preserve state) ──── */}
        <div role="tabpanel" id="panel-benchmark" aria-labelledby="tab-benchmark" className={view === "benchmark" ? "" : "hidden"}>
          <BenchmarkTab />
        </div>
        <div role="tabpanel" id="panel-failures" aria-labelledby="tab-failures" className={view === "failures" ? "" : "hidden"}>
          <FailuresTab />
        </div>
        <div role="tabpanel" id="panel-policies" aria-labelledby="tab-policies" className={view === "policies" ? "" : "hidden"}>
          <PoliciesTab />
        </div>
      </div>
    </div>
  );
}
