interface LoadingSkeletonProps {
  variant: "table" | "cards" | "detail";
}

export default function LoadingSkeleton({ variant }: LoadingSkeletonProps) {
  if (variant === "table") {
    return (
      <div className="space-y-2">
        {Array.from({ length: 4 }).map((_, i) => (
          <div key={i} className="skeleton h-11" />
        ))}
      </div>
    );
  }

  if (variant === "cards") {
    return (
      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
        {Array.from({ length: 4 }).map((_, i) => (
          <div key={i} className="p-px rounded-xl bg-white/[0.03]">
            <div className="skeleton h-32 rounded-lg" />
          </div>
        ))}
      </div>
    );
  }

  // detail — card-style shimmer with double-bezel skeleton
  return (
    <div className="space-y-4">
      {[1, 2].map((i) => (
        <div key={i} className="p-px rounded-xl bg-white/[0.03]">
          <div className="bg-bg-card rounded-lg p-5 space-y-3">
            <div className="skeleton h-3 w-20" />
            <div className="skeleton h-4 w-full" />
            <div className="skeleton h-4 w-3/4" />
            <div className="skeleton h-3 w-16 mt-2" />
          </div>
        </div>
      ))}
    </div>
  );
}
