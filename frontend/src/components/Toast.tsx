import { useState, useEffect, useCallback } from "react";
import { motion, AnimatePresence } from "framer-motion";
import { X, CheckCircle, AlertCircle, Info } from "lucide-react";
import { subscribe, type ToastData } from "../lib/toast";

const MAX_TOASTS = 3;
const AUTO_DISMISS_MS = 3000;

const variantConfig: Record<
  ToastData["variant"],
  { icon: typeof CheckCircle; border: string; bgGlow: string }
> = {
  success: {
    icon: CheckCircle,
    border: "border-[#2dd4bf]/40",
    bgGlow: "bg-[#2dd4bf]/8",
  },
  error: {
    icon: AlertCircle,
    border: "border-[#ef4444]/40",
    bgGlow: "bg-[#ef4444]/8",
  },
  info: {
    icon: Info,
    border: "border-[#7ab8f7]/40",
    bgGlow: "bg-[#7ab8f7]/8",
  },
};

export default function ToastContainer() {
  const [toasts, setToasts] = useState<ToastData[]>([]);

  const removeToast = useCallback((id: string) => {
    setToasts((prev) => prev.filter((t) => t.id !== id));
  }, []);

  useEffect(() => {
    const unsub = subscribe((toast) => {
      setToasts((prev) => {
        const next = [toast, ...prev].slice(0, MAX_TOASTS);
        return next;
      });
    });
    return unsub;
  }, []);

  return (
    <div className="fixed top-4 right-4 z-[100] flex flex-col gap-2 pointer-events-none">
      <AnimatePresence mode="popLayout">
        {toasts.map((t) => (
          <ToastItem
            key={t.id}
            toast={t}
            onDismiss={() => removeToast(t.id)}
          />
        ))}
      </AnimatePresence>
    </div>
  );
}

function ToastItem({
  toast,
  onDismiss,
}: {
  toast: ToastData;
  onDismiss: () => void;
}) {
  useEffect(() => {
    const timer = setTimeout(onDismiss, AUTO_DISMISS_MS);
    return () => clearTimeout(timer);
  }, [onDismiss]);

  const config = variantConfig[toast.variant];
  const Icon = config.icon;

  return (
    <motion.div
      layout
      initial={{ opacity: 0, x: 80, scale: 0.95 }}
      animate={{ opacity: 1, x: 0, scale: 1 }}
      exit={{ opacity: 0, x: 80, scale: 0.95 }}
      transition={{ duration: 0.25, ease: "easeOut" }}
      className={`
        pointer-events-auto flex items-start gap-3
        min-w-[300px] max-w-[420px]
        bg-bg-card border rounded-xl p-4
        shadow-lg backdrop-blur-md
        ${config.border} ${config.bgGlow}
      `}
    >
      <Icon className="w-5 h-5 shrink-0 mt-0.5 text-fg-primary" />
      <p className="flex-1 text-sm text-fg-primary font-mono leading-relaxed min-w-0">
        {toast.message}
      </p>
      <button
        onClick={onDismiss}
        className="shrink-0 p-0.5 rounded text-fg-muted hover:text-fg-primary hover:bg-white/[0.06] transition-colors"
        aria-label="Dismiss"
      >
        <X className="w-4 h-4" />
      </button>
    </motion.div>
  );
}
