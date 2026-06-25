import { CheckCircle2, XCircle } from "lucide-react";
import { motion } from "framer-motion";

interface StatusBadgeProps {
  status: "success" | "failed" | "running" | string;
  label?: string;
  variant?: "default" | "compact" | "dot";
}

const DEFAULT_LABELS: Record<string, string> = {
  success: "Success",
  failed: "Failed",
  running: "Running",
};

export default function StatusBadge({
  status,
  label,
  variant = "default",
}: StatusBadgeProps) {
  const resolvedLabel = label ?? DEFAULT_LABELS[status] ?? status;

  if (variant === "compact") {
    const colorMap: Record<string, string> = {
      success: "text-accent bg-accent/8 border-accent/15",
      failed: "text-destructive bg-destructive/8 border-destructive/15",
      running: "text-accent-amber bg-accent-amber/8 border-accent-amber/15",
    };
    return (
      <span
        className={`text-[11px] font-mono font-semibold px-2.5 py-0.5 rounded-full border ${
          colorMap[status] ?? "text-fg-muted bg-bg-hover border-border"
        }`}
      >
        {resolvedLabel}
      </span>
    );
  }

  if (variant === "dot") {
    const colorMap: Record<string, { dot: string; text: string }> = {
      success: { dot: "bg-emerald-400", text: "text-emerald-400" },
      failed: { dot: "bg-red-400", text: "text-red-400" },
      running: { dot: "bg-amber-400", text: "text-amber-400" },
    };
    const c = colorMap[status] ?? { dot: "bg-fg-muted", text: "text-fg-muted" };
    return (
      <span className={`flex items-center gap-1.5 text-xs ${c.text}`}>
        <span className={`w-1.5 h-1.5 rounded-full ${c.dot}`} />
        {resolvedLabel}
      </span>
    );
  }

  switch (status) {
    case "running":
      return (
        <span className="inline-flex items-center gap-2 text-sm">
          <span className="relative flex h-2.5 w-2.5">
            <motion.span
              animate={{ opacity: [1, 0.3, 1], scale: [1, 0.85, 1] }}
              transition={{ duration: 1.8, repeat: Infinity, ease: "easeInOut" }}
              className="absolute inset-0 rounded-full bg-accent"
            />
            <motion.span
              animate={{ opacity: [0.6, 0, 0.6], scale: [1, 1.8, 1] }}
              transition={{ duration: 1.8, repeat: Infinity, ease: "easeInOut" }}
              className="absolute inset-0 rounded-full bg-accent"
            />
          </span>
          <span className="text-accent font-medium">{resolvedLabel}</span>
        </span>
      );
    case "success":
      return (
        <span className="inline-flex items-center gap-1.5 text-sm">
          <CheckCircle2 className="w-4 h-4 text-accent" />
          <span className="text-accent font-medium">{resolvedLabel}</span>
        </span>
      );
    case "failed":
      return (
        <span className="inline-flex items-center gap-1.5 text-sm">
          <XCircle className="w-4 h-4 text-destructive" />
          <span className="text-destructive font-medium">{resolvedLabel}</span>
        </span>
      );
    default:
      return <span className="text-fg-muted text-sm font-medium">{resolvedLabel}</span>;
  }
}
