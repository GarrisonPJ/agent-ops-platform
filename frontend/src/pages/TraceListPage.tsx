import { useState, useMemo, useEffect } from "react";
import { useNavigate } from "react-router-dom";
import {
  createColumnHelper,
  flexRender,
  getCoreRowModel,
  useReactTable,
} from "@tanstack/react-table";
import { Search, List, ChevronDown } from "lucide-react";
import { motion } from "framer-motion";
import { useGetTracesQuery } from "../services/api";
import type { TrajectorySummary } from "../types";
import StatusBadge from "../components/StatusBadge";
import EmptyState from "../components/EmptyState";
import LoadingSkeleton from "../components/LoadingSkeleton";

function RelativeTime({ dateStr }: { dateStr: string }) {
  const date = useMemo(() => {
    const d = new Date(dateStr);
    return isNaN(d.getTime()) ? null : d;
  }, [dateStr]);

  if (!date) return <span className="text-fg-muted">--</span>;

  const diff = Date.now() - date.getTime();
  const minutes = Math.floor(diff / 60000);
  if (minutes < 1) return <span className="text-fg-muted">刚刚</span>;
  if (minutes < 60)
    return <span className="text-fg-muted">{minutes}分钟前</span>;
  const hours = Math.floor(minutes / 60);
  if (hours < 24)
    return <span className="text-fg-muted">{hours}小时前</span>;
  const days = Math.floor(hours / 24);
  return <span className="text-fg-muted">{days}天前</span>;
}

const columnHelper = createColumnHelper<TrajectorySummary>();

const columns = [
  columnHelper.accessor("id", {
    header: "ID",
    cell: (info) => (
      <span className="font-mono text-xs text-fg-muted">
        #TR-{info.getValue().substring(0, 4).toUpperCase()}
      </span>
    ),
  }),
  columnHelper.accessor("task", {
    header: "任务",
    cell: (info) => (
      <span className="font-medium text-sm cursor-pointer hover:text-accent transition-colors truncate block max-w-full">
        {info.getValue()}
      </span>
    ),
  }),
  columnHelper.accessor("status", {
    header: "状态",
    cell: (info) => {
      const status = info.getValue();
      const labels: Record<string, string> = { success: "Success", failed: "Failed", running: "Running" };
      return <StatusBadge status={status} label={labels[status]} variant="dot" />;
    },
  }),
  columnHelper.accessor("step_count", {
    header: "步数",
    cell: (info) => (
      <span className="text-sm text-fg-muted tabular-nums">
        {info.getValue()}
      </span>
    ),
  }),
  columnHelper.accessor("created_at", {
    header: "时间",
    cell: (info) => <RelativeTime dateStr={info.getValue()} />,
  }),
  columnHelper.accessor("score", {
    header: "评分",
    cell: (info) => {
      const val = info.getValue();
      return val != null ? (
        <span className="text-sm text-accent-amber tabular-nums font-mono">
          {val.toFixed(2)}
        </span>
      ) : (
        <span className="text-sm text-fg-muted">--</span>
      );
    },
  }),
];

export default function TraceListPage() {
  const navigate = useNavigate();
  const [statusFilter, setStatusFilter] = useState("");
  const [searchInput, setSearchInput] = useState("");
  const [debouncedFilter, setDebouncedFilter] = useState("");

  useEffect(() => {
    const timer = setTimeout(() => setDebouncedFilter(searchInput), 300);
    return () => clearTimeout(timer);
  }, [searchInput]);

  const { data: traces, isLoading } = useGetTracesQuery({
    status: statusFilter || undefined,
    tool: debouncedFilter || undefined,
  });

  const trajectories = traces?.trajectories ?? [];

  const table = useReactTable({
    data: trajectories,
    columns,
    getCoreRowModel: getCoreRowModel(),
  });

  const containerVariants = {
    hidden: {},
    visible: {
      transition: { staggerChildren: 0.03 },
    },
  };

  const rowVariants = {
    hidden: { opacity: 0, y: 8 },
    visible: { opacity: 1, y: 0 },
  };

  return (
    <div className="flex-1 flex flex-col">
      <div className="max-w-5xl mx-auto px-6 pt-8 pb-6 w-full shrink-0">
        <div className="flex items-center justify-between">
          <div>
            <h1 className="text-2xl font-semibold tracking-tight text-fg-primary">Traces</h1>
            <p className="text-sm text-fg-muted mt-1">Execution history & trajectories.</p>
          </div>
          <div className="relative">
            <Search className="w-4 h-4 absolute left-3 top-1/2 -translate-y-1/2 text-fg-muted" strokeWidth={1.5} />
            <input
              type="text"
              value={searchInput}
              onChange={(e) => setSearchInput(e.target.value)}
              placeholder="Search…"
              aria-label="Search trajectories"
              inputMode="search"
              autoComplete="off"
              spellCheck={false}
              className="bg-bg-card border border-border shadow-inner-glow rounded-md pl-9 pr-4 py-2 text-sm outline-none focus-visible:border-accent/50 focus-visible:ring-1 focus-visible:ring-accent/50 w-64 text-fg-primary placeholder-fg-muted/50 transition-colors duration-150 ease-out"
            />
          </div>
        </div>
      </div>

      <motion.div
        initial={{ opacity: 0, y: 16 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: 0.6, ease: [0.16, 1, 0.3, 1] }}
        className="flex-1 overflow-auto max-w-5xl mx-auto px-6 pb-8 w-full"
      >
        {/* Filters */}
        <div className="flex gap-3 mb-6">
          <div className="relative">
            <select
              aria-label="Filter by status"
              value={statusFilter}
              onChange={(e) => setStatusFilter(e.target.value)}
              className="appearance-none bg-bg-card shadow-inner-glow border border-border rounded-md pl-3 pr-8 py-2 text-sm text-fg-primary font-mono outline-none focus-visible:border-accent/50 focus-visible:ring-1 focus-visible:ring-accent/50"
            >
              <option value="">All Statuses</option>
              <option value="running">Running</option>
              <option value="success">Success</option>
              <option value="failed">Failed</option>
            </select>
            <ChevronDown className="w-3.5 h-3.5 absolute right-2.5 top-1/2 -translate-y-1/2 text-fg-muted pointer-events-none" strokeWidth={1.5} />
          </div>
        </div>

        {/* Content */}
        {isLoading ? (
          <LoadingSkeleton variant="table" />
        ) : trajectories.length > 0 ? (
          <div className="bg-bg-card border border-border rounded-md overflow-hidden shadow-inner-glow">
            <table className="w-full">
              <thead>
                {table.getHeaderGroups().map((headerGroup) => (
                  <tr
                    key={headerGroup.id}
                    className="border-b border-border bg-white/[0.02]"
                  >
                    {headerGroup.headers.map((header) => (
                      <th
                        key={header.id}
                        className="text-left text-[11px] text-fg-muted font-semibold font-mono px-5 py-3 uppercase tracking-wider"
                      >
                        {flexRender(
                          header.column.columnDef.header,
                          header.getContext(),
                        )}
                      </th>
                    ))}
                  </tr>
                ))}
              </thead>
              <motion.tbody
                variants={containerVariants}
                initial="hidden"
                animate="visible"
              >
                {table.getRowModel().rows.map((row) => (
                  <motion.tr
                    key={row.id}
                    variants={rowVariants}
                    role="button"
                    tabIndex={0}
                    aria-label={`View trace ${row.original.id}`}
                    onClick={() => navigate(`/traces/${row.original.id}`)}
                    onKeyDown={(e) => {
                      if (e.key === "Enter" || e.key === " ") {
                        e.preventDefault();
                        navigate(`/traces/${row.original.id}`);
                      }
                    }}
                    className="border-b border-border last:border-b-0 hover:bg-white/[0.04] cursor-pointer transition-colors duration-150"
                  >
                    {row.getVisibleCells().map((cell) => (
                      <td key={cell.id} className="px-5 py-4">
                        {flexRender(
                          cell.column.columnDef.cell,
                          cell.getContext(),
                        )}
                      </td>
                    ))}
                  </motion.tr>
                ))}
              </motion.tbody>
            </table>
            <div className="px-4 py-3 border-t border-border bg-bg-root text-[11px] text-fg-muted font-mono flex items-center justify-between">
              <span>
                Showing {trajectories.length} trajectories
                {statusFilter ? (
                  <span> (filtered: {statusFilter})</span>
                ) : null}
              </span>
            </div>
          </div>
        ) : (
          /* Empty state */
          <EmptyState
            icon={List}
            message="No Traces Found"
            description="Execute an Agent task to see it here."
            actionLabel="Go to Run"
            onAction={() => navigate("/run")}
          />
        )}
      </motion.div>
    </div>
  );
}
