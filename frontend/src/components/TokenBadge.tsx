import { ArrowUp, ArrowDown } from "lucide-react";

interface TokenBadgeProps {
  prompt: number | null;
  completion: number | null;
}

function fmt(n: number): string {
  if (n >= 1000) {
    return (n / 1000).toFixed(n >= 10000 ? 0 : 1) + "k";
  }
  return String(n);
}

export default function TokenBadge({ prompt, completion }: TokenBadgeProps) {
  return (
    <span className="inline-flex items-center gap-2 text-[11px] font-mono text-fg-muted">
      {prompt != null && (
        <span className="inline-flex items-center gap-0.5 tabular-nums">
          <ArrowUp className="w-3 h-3 text-accent" />
          {fmt(prompt)}
          <span className="text-fg-muted ml-0.5">prompt</span>
        </span>
      )}
      {completion != null && (
        <span className="inline-flex items-center gap-0.5 tabular-nums">
          <ArrowDown className="w-3 h-3 text-info" />
          {fmt(completion)}
          <span className="text-fg-muted ml-0.5">completion</span>
        </span>
      )}
      {prompt == null && completion == null && (
        <span className="text-fg-muted italic">--</span>
      )}
    </span>
  );
}
