import { motion } from "framer-motion";
import {
  ResponsiveContainer,
  RadarChart,
  Radar,
  PolarGrid,
  PolarAngleAxis,
  PolarRadiusAxis,
  BarChart,
  Bar,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
} from "recharts";
import type { FailureSummary } from "../types";
import LoadingSkeleton from "./LoadingSkeleton";
import ErrorBanner from "./ErrorBanner";
import EmptyState from "./EmptyState";
import { Warning } from "@phosphor-icons/react";

/* ------------------------------------------------------------------ */
/*  Props                                                              */
/* ------------------------------------------------------------------ */

interface FailureDistributionChartProps {
  data: FailureSummary | null;
  loading: boolean;
  error: string | null;
}

/* ------------------------------------------------------------------ */
/*  Color map                                                          */
/* ------------------------------------------------------------------ */

const DIMENSION_COLORS: Record<string, string> = {
  planning: "#4b8cf7",
  execution: "#ef4444",
  context: "#f5a623",
  budget: "#2dd4bf",
};

const DIMENSION_LABELS: Record<string, string> = {
  planning: "Planning",
  execution: "Execution",
  context: "Context",
  budget: "Budget",
};

/* ------------------------------------------------------------------ */
/*  Chart tooltip                                                      */
/* ------------------------------------------------------------------ */

function ChartTooltip({
  active,
  payload,
  label,
}: {
  active?: boolean;
  payload?: { name: string; value: number; color: string }[];
  label?: string;
}) {
  if (!active || !payload || payload.length === 0) return null;
  return (
    <div className="bg-bg-card-raised border border-border rounded-lg px-3 py-2 shadow-card text-xs font-mono">
      <p className="text-fg-muted mb-1">{label || payload[0].name}</p>
      {payload.map((entry) => (
        <p key={entry.name} style={{ color: entry.color }}>
          {DIMENSION_LABELS[entry.name] ?? entry.name}: {(entry.value * 100).toFixed(1)}%
        </p>
      ))}
    </div>
  );
}

/* ------------------------------------------------------------------ */
/*  Bar tooltip                                                        */
/* ------------------------------------------------------------------ */

function BarTooltip({
  active,
  payload,
  label,
}: {
  active?: boolean;
  payload?: { name: string; value: number; color: string }[];
  label?: string;
}) {
  if (!active || !payload || payload.length === 0) return null;
  return (
    <div className="bg-bg-card-raised border border-border rounded-lg px-3 py-2 shadow-card text-xs font-mono">
      <p className="text-fg-muted mb-1">{label}</p>
      {payload.map((entry) => (
        <p key={entry.name} style={{ color: entry.color }}>
          {DIMENSION_LABELS[entry.name] ?? entry.name}: {(entry.value * 100).toFixed(1)}%
        </p>
      ))}
    </div>
  );
}

/* ------------------------------------------------------------------ */
/*  Main component                                                     */
/* ------------------------------------------------------------------ */

export default function FailureDistributionChart({
  data,
  loading,
  error,
}: FailureDistributionChartProps) {
  if (loading) {
    return (
      <motion.div
        initial={{ opacity: 0 }}
        animate={{ opacity: 1 }}
        className="bg-bg-card border border-border rounded-lg p-6"
      >
        <LoadingSkeleton variant="detail" />
      </motion.div>
    );
  }

  if (error) {
    return (
      <motion.div
        initial={{ opacity: 0, y: -8 }}
        animate={{ opacity: 1, y: 0 }}
      >
        <ErrorBanner title="Failed to load failure data" message={error} />
      </motion.div>
    );
  }

  if (!data) {
    return (
      <motion.div
        initial={{ opacity: 0, y: 16 }}
        animate={{ opacity: 1, y: 0 }}
      >
        <EmptyState
          icon={Warning}
          message="No failure data available"
          description="Run an analysis on a trajectory to see failure distribution"
        />
      </motion.div>
    );
  }

  const chartData = [
    { name: "planning", value: data.planning, fill: DIMENSION_COLORS.planning },
    { name: "execution", value: data.execution, fill: DIMENSION_COLORS.execution },
    { name: "context", value: data.context, fill: DIMENSION_COLORS.context },
    { name: "budget", value: data.budget, fill: DIMENSION_COLORS.budget },
  ];

  return (
    <motion.div
      initial={{ opacity: 0, y: 12 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.4, ease: [0.16, 1, 0.3, 1] }}
      className="bg-bg-card border border-border rounded-lg p-6"
    >
      <h3 className="text-xs font-mono text-fg-muted uppercase tracking-wider mb-6">
        Failure Distribution
      </h3>

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        {/* Radar Chart */}
        <div className="h-72">
          <ResponsiveContainer width="100%" height="100%">
            <RadarChart data={chartData}>
              <PolarGrid stroke="rgb(255 255 255 / 0.08)" />
              <PolarAngleAxis
                dataKey="name"
                tick={{
                  fill: "#878d96",
                  fontSize: 11,
                  fontFamily: "Geist Mono, monospace",
                }}
                tickFormatter={(name: string) => DIMENSION_LABELS[name] ?? name}
              />
              <PolarRadiusAxis
                angle={90}
                domain={[0, 1]}
                tick={{
                  fill: "#52525b",
                  fontSize: 10,
                  fontFamily: "Geist Mono, monospace",
                }}
                tickFormatter={(v: number) => `${(v * 100).toFixed(0)}%`}
              />
              <Radar
                name="failure_rate"
                dataKey="value"
                stroke="#4b8cf7"
                fill="#4b8cf7"
                fillOpacity={0.2}
                strokeWidth={1.5}
              />
              <Tooltip content={<ChartTooltip />} />
            </RadarChart>
          </ResponsiveContainer>
        </div>

        {/* Bar Chart */}
        <div className="h-72">
          <ResponsiveContainer width="100%" height="100%">
            <BarChart data={chartData} margin={{ top: 8, right: 8, bottom: 0, left: 0 }}>
              <CartesianGrid strokeDasharray="3 3" stroke="rgb(255 255 255 / 0.06)" />
              <XAxis
                dataKey="name"
                tick={{
                  fill: "#878d96",
                  fontSize: 11,
                  fontFamily: "Geist Mono, monospace",
                }}
                tickFormatter={(name: string) => DIMENSION_LABELS[name] ?? name}
              />
              <YAxis
                domain={[0, 1]}
                tick={{
                  fill: "#52525b",
                  fontSize: 10,
                  fontFamily: "Geist Mono, monospace",
                }}
                tickFormatter={(v: number) => `${(v * 100).toFixed(0)}%`}
              />
              <Tooltip content={<BarTooltip />} />
              <Bar
                dataKey="value"
                radius={[4, 4, 0, 0]}
                maxBarSize={48}
                shape={(props: { x: number; y: number; width: number; height: number; index: number }) => {
                  const { x, y, width, height, index } = props;
                  const fill = chartData[index]?.fill ?? "#4b8cf7";
                  return <rect x={x} y={y} width={width} height={height} rx={4} ry={4} fill={fill} />;
                }}
              />
            </BarChart>
          </ResponsiveContainer>
        </div>
      </div>
    </motion.div>
  );
}
