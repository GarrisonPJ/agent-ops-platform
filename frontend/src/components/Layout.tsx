import { NavLink, useLocation } from "react-router-dom";
import { GitCompare, List, Wrench, Play, FlaskConical } from "lucide-react";
import { cn } from "../lib/utils";
import ToastContainer from "./Toast";

const navItems = [
  { to: "/run", label: "Run", icon: Play },
  { to: "/traces", label: "Traces", icon: List },
  { to: "/tools", label: "Tools", icon: Wrench },
  { to: "/compare", label: "Compare", icon: GitCompare },
  { to: "/eval", label: "Eval", icon: FlaskConical },
];

export default function Layout({ children }: { children: React.ReactNode }) {
  const location = useLocation();
  const isRunPage = location.pathname === "/run";

  return (
    <div className="flex flex-col h-[100dvh] w-[100dvw] bg-bg-root overflow-hidden">
      {/* ── Ambient glow (behind everything) ──────────── */}
      <div className={`ambient-glow${isRunPage ? "" : " active"}`} />

      {/* ── Edge-to-edge IDE-style topbar ─────────────── */}
      <header className="relative z-40 flex items-center justify-between px-4 h-12 shrink-0 border-b border-border bg-bg-root/80 backdrop-blur-md">
        <div className="flex items-center gap-6 h-full">
          {/* Logo / Brand */}
          <div className="flex items-center gap-2 mr-4">
            <div className="w-5 h-5 rounded-md bg-accent flex items-center justify-center shadow-inner-glow-accent">
              <Play className="w-3 h-3 text-white" strokeWidth={2.5} />
            </div>
            <span className="font-mono font-semibold text-sm tracking-tight text-fg-primary">
              AgentOps
            </span>
          </div>

          {/* Navigation */}
          <nav className="flex items-center h-full gap-1">
            {navItems.map((item) => {
              const isActive =
                item.to === "/"
                  ? location.pathname === "/"
                  : location.pathname.startsWith(item.to);
              const Icon = item.icon;
              return (
                <NavLink
                  key={item.to}
                  to={item.to}
                  className={cn(
                    "relative flex items-center gap-2 px-3 h-8 rounded-md text-sm font-medium transition-colors duration-150 ease-out",
                    isActive
                      ? "text-fg-primary bg-white/[0.04] shadow-inner-glow"
                      : "text-fg-muted hover:text-fg-primary hover:bg-white/[0.02]"
                  )}
                >
                  <Icon className="w-4 h-4" strokeWidth={1.5} />
                  <span className="hidden sm:inline relative z-10">{item.label}</span>
                </NavLink>
              );
            })}
          </nav>
        </div>

        {/* Global actions (e.g. settings, profile) could go here */}
      </header>

      {/* ── Page content (occupies remaining height) ─── */}
      <main className="relative z-10 flex-1 overflow-hidden">{children}</main>

      {/* ── Toast notifications ─────────────────────────── */}
      <ToastContainer />
    </div>
  );
}
