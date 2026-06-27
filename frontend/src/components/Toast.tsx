import { useState, useEffect, useCallback } from "react";
import { motion, AnimatePresence } from "framer-motion";
import { X, CheckCircle, WarningCircle, Info } from "@phosphor-icons/react";
import { subscribe, type ToastData } from "../lib/toast";

const MAX_TOASTS = 3;
const AUTO_DISMISS_MS = 3000;

const variantConfig: Record<
  ToastData["variant"],
  { icon: typeof CheckCircle; border: string; bgClass: string; iconColor: string }
> = {
  success: {
    icon: CheckCircle,
    border: "border-[#2dd4bf]/30",
    bgClass: "bg-bg-card/80",
    iconColor: "text-[#2dd4bf]",
  },
  error: {
    icon: WarningCircle,
    border: "border-[#ef4444]/30",
    bgClass: "bg-bg-card/80",
    iconColor: "text-[#ef4444]",
  },
  info: {
    icon: Info,
    border: "border-[#7ab8f7]/30",
    bgClass: "bg-bg-card/80",
    iconColor: "text-[#7ab8f7]",
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
    <div className="fixed top-4 right-4 z-[100] flex flex-col gap-2 pointer-events-none overscroll-behavior-contain">
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
        relative overflow-hidden pointer-events-auto flex items-start gap-3
        w-[360px]
        border rounded-xl p-4
        shadow-card-raised backdrop-blur-xl
        ${config.bgClass} ${config.border}
      `}
    >
      <Icon className={`w-[20px] h-[20px] shrink-0 mt-0.5 ${config.iconColor}`} weight="fill" />
      <div className="flex-1 flex flex-col gap-1 min-w-0">
        <span className="text-[13px] font-medium text-fg-primary tracking-tight">
          {toast.variant.charAt(0).toUpperCase() + toast.variant.slice(1)}
        </span>
        <p className="text-[12px] text-fg-muted font-mono leading-relaxed truncate whitespace-normal break-words max-h-[40px] overflow-hidden">
          {toast.message}
        </p>
      </div>
      <button
        onClick={onDismiss}
        className="shrink-0 text-fg-subtle hover:text-fg-primary transition-colors"
        aria-label="Dismiss"
      >
        <X className="w-4 h-4" weight="bold" />
      </button>
    </motion.div>
  );
}
