import type { LucideIcon } from "lucide-react";
import { motion } from "framer-motion";

interface EmptyStateProps {
  icon: LucideIcon;
  message: string;
  description?: string;
  actionLabel?: string;
  onAction?: () => void;
}

export default function EmptyState({
  icon: Icon,
  message,
  description,
  actionLabel,
  onAction,
}: EmptyStateProps) {
  return (
    <motion.div
      initial={{ opacity: 0, y: 16 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.6, ease: [0.16, 1, 0.3, 1] }}
      className="flex flex-col items-center justify-center py-24 text-center"
    >
      <span className="
        flex items-center justify-center w-16 h-16 rounded-2xl
        bg-white/[0.03] ring-1 ring-white/[0.06]
        mb-5
      ">
        <Icon className="w-7 h-7 text-fg-subtle" />
      </span>
      <p className="text-fg-muted text-sm font-medium mb-1.5">{message}</p>
      {description && (
        <p className="text-fg-subtle text-xs mb-6 max-w-xs leading-relaxed">{description}</p>
      )}
      {actionLabel && onAction && (
        <button
          onClick={onAction}
          className="
            bg-accent text-bg-root font-semibold font-mono
            rounded-full px-6 py-2.5 text-sm
            hover:shadow-glow-accent hover:scale-[1.02]
            active:scale-[0.98]
            transition-colors transition-transform duration-500 ease-spring
          "
        >
          {actionLabel}
        </button>
      )}
    </motion.div>
  );
}
