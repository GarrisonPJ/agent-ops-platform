import { motion } from "framer-motion";

interface ContextWindowBarProps {
  used: number;
  limit: number;
}

/** Slim progress bar (4px height) showing context-window usage. */
export default function ContextWindowBar({ used, limit }: ContextWindowBarProps) {
  const pct = limit > 0 ? Math.min((used / limit) * 100, 100) : 0;

  return (
    <div className="w-24 h-1 bg-border rounded-full overflow-hidden" title={`${used.toLocaleString()} / ${limit.toLocaleString()} tokens`}>
      <motion.div
        className="h-full rounded-full"
        style={{ backgroundColor: "#22C55E" }}
        initial={{ width: 0 }}
        animate={{ width: `${pct}%` }}
        transition={{ duration: 0.4, ease: "easeOut" }}
      />
    </div>
  );
}
