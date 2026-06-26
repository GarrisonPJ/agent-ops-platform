import { useSearchParams } from "react-router-dom";
import {
  FlaskConical,
  Bug,
  Shield,
} from "lucide-react";
import BenchmarkTab from "./eval/BenchmarkTab";
import FailuresTab from "./eval/FailuresTab";
import PoliciesTab from "./eval/PoliciesTab";

const TABS: { key: string; label: string; icon: typeof FlaskConical }[] = [
  { key: "benchmark", label: "Benchmark", icon: FlaskConical },
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
    <div>
      {/* Page header */}
      <header className="border-b border-border px-6 py-4">
        <div className="max-w-6xl mx-auto flex items-center gap-3">
          <FlaskConical className="w-5 h-5 text-accent" />
          <h1 className="text-sm font-mono font-semibold text-fg-primary">
            Evaluations
          </h1>
        </div>
      </header>

      <main className="max-w-4xl mx-auto px-6 py-8 space-y-6">
        {/* ── Tab navigation ─────────────────────────────────────── */}
        <div className="flex items-center gap-1 bg-bg-card border border-border rounded-lg p-0.5">
          {TABS.map((tab) => {
            const Icon = tab.icon;
            const active = view === tab.key;
            return (
              <button
                key={tab.key}
                onClick={() => setView(tab.key)}
                className={`flex items-center gap-2 px-4 py-1.5 text-xs font-mono rounded-md transition-all duration-200 ${
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

        {/* ── Active tab content ──────────────────────────────────── */}
        {view === "benchmark" && <BenchmarkTab />}
        {view === "failures" && <FailuresTab />}
        {view === "policies" && <PoliciesTab />}
      </main>
    </div>
  );
}
