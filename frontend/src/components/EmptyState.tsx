import { motion } from "framer-motion";

interface EmptyStateProps {
  icon: React.ElementType;
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
      <span className="mb-6 opacity-10 flex items-center justify-center pointer-events-none">
        <Icon className="w-32 h-32 text-fg-primary" weight="light" />
      </span>
      <h3 className="text-fg-primary text-[15px] font-mono uppercase tracking-[0.2em] mb-2">{message}</h3>
      {description && (
        <p className="text-fg-muted text-[13px] mb-8 max-w-sm leading-relaxed">{description}</p>
      )}
      {actionLabel && onAction && (
        <button
          onClick={onAction}
          className="
            bg-fg-primary text-bg-root font-semibold font-mono uppercase tracking-wider
            rounded-md px-8 py-3 text-xs
            hover:opacity-90 active:scale-[0.98]
            transition-all duration-300 shadow-[0_2px_10px_rgba(255,255,255,0.05)]
          "
        >
          {actionLabel}
        </button>
      )}
    </motion.div>
  );
}
