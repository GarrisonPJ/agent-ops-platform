import { Flask, Plus } from "@phosphor-icons/react";
import { NavLink, useLocation } from "react-router-dom";
import { cn } from "../lib/utils";
import ToastContainer from "./Toast";

const IS_RECORDED_DEMO = import.meta.env.VITE_MOCK_API === "true";

export default function Phase1Layout({ children }: { children: React.ReactNode }) {
  const location = useLocation();

  return (
    <div className="flex min-h-[100dvh] w-full flex-col bg-bg-root text-fg-primary">
      <div className="ambient-glow active" />
      {IS_RECORDED_DEMO ? (
        <div
          role="status"
          className="relative z-50 border-b border-accent-amber/20 bg-accent-amber/10 px-4 py-1.5 text-center font-mono text-[11px] font-medium tracking-wide text-accent-amber"
        >
          Recorded Demo Data · deterministic playback · no live model calls
        </div>
      ) : null}

      <header className="sticky top-0 z-40 shrink-0 border-b border-border bg-bg-root/85 backdrop-blur-xl">
        <div className="mx-auto flex h-14 w-full max-w-6xl items-center justify-between px-4 sm:px-6">
          <div className="flex min-w-0 items-center gap-4 sm:gap-8">
            <NavLink
              to="/experiments"
              className="flex shrink-0 items-center gap-2 rounded-md"
              aria-label="AgentOps experiments"
            >
              <span className="flex h-6 w-6 items-center justify-center rounded-md bg-accent shadow-inner-glow-accent">
                <Flask className="h-3.5 w-3.5 text-white" weight="fill" />
              </span>
              <span className="hidden font-mono text-sm font-semibold tracking-tight sm:inline">
                AgentOps
              </span>
            </NavLink>

            <nav aria-label="Primary navigation">
              <NavLink
                to="/experiments"
                className={cn(
                  "rounded-md px-3 py-2 text-sm font-medium transition-colors",
                  location.pathname !== "/experiments/new" &&
                    (location.pathname.startsWith("/experiments") || location.pathname.startsWith("/runs"))
                    ? "bg-white/[0.05] text-fg-primary shadow-inner-glow"
                    : "text-fg-muted hover:bg-white/[0.03] hover:text-fg-primary",
                )}
              >
                Experiments
              </NavLink>
            </nav>
          </div>

          <NavLink
            to="/experiments/new"
            className={cn(
              "inline-flex items-center gap-2 rounded-md px-3 py-2 text-sm font-semibold transition-colors",
              location.pathname === "/experiments/new"
                ? "bg-accent text-white"
                : "bg-fg-primary text-bg-root hover:bg-white/90",
            )}
          >
            <Plus className="h-4 w-4" weight="bold" />
            <span className="hidden sm:inline">New experiment</span>
            <span className="sm:hidden">New</span>
          </NavLink>
        </div>
      </header>

      <main className="relative z-10 flex min-h-0 flex-1 flex-col">{children}</main>
      <ToastContainer />
    </div>
  );
}
