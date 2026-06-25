import { AlertCircle } from "lucide-react";
import { motion } from "framer-motion";

interface ErrorBannerProps {
  title?: string;
  message: string;
  onRetry?: () => void;
}

export default function ErrorBanner({
  title = "Error",
  message,
  onRetry,
}: ErrorBannerProps) {
  return (
    <motion.div
      initial={{ opacity: 0, y: -6 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.4, ease: [0.16, 1, 0.3, 1] }}
      className="
        p-px rounded-xl
        bg-gradient-to-b from-destructive/15 to-destructive/5
      "
    >
      <div className="
        bg-destructive/[0.06] rounded-lg p-4
        flex items-start gap-3
      ">
        <span className="
          flex items-center justify-center w-8 h-8 rounded-xl
          bg-destructive/10 ring-1 ring-destructive/20 flex-shrink-0
        ">
          <AlertCircle className="w-4 h-4 text-destructive" />
        </span>
        <div className="flex-1 min-w-0">
          <p className="text-destructive font-semibold text-sm">{title}</p>
          <p className="text-fg-muted text-sm mt-1 break-all leading-relaxed">{message}</p>
        </div>
        {onRetry && (
          <button
            onClick={onRetry}
            className="
              flex-shrink-0 text-xs text-accent font-mono font-medium
              px-3 py-1.5 rounded-full
              bg-accent/8 border border-accent/15
              hover:bg-accent/12
              transition-all duration-300 ease-spring
            "
          >
            Retry
          </button>
        )}
      </div>
    </motion.div>
  );
}
