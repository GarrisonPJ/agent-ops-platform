import { ArrowRight, Flask, MagnifyingGlass, Plus } from "@phosphor-icons/react";
import { useMemo, useState } from "react";
import { Link } from "react-router-dom";
import EmptyState from "../components/EmptyState";
import ErrorBanner from "../components/ErrorBanner";
import LifecycleBadge from "../components/LifecycleBadge";
import LoadingSkeleton from "../components/LoadingSkeleton";
import { formatDate, formatScore, getApiErrorMessage } from "../lib/phase1Format";
import { useGetExperimentsQuery } from "../services/experimentsApi";

export default function ExperimentsPage() {
  const [search, setSearch] = useState("");
  const { data = [], error, isLoading, refetch } = useGetExperimentsQuery();

  const experiments = useMemo(() => {
    const query = search.trim().toLowerCase();
    if (!query) return data;
    return data.filter(
      (experiment) =>
        experiment.name.toLowerCase().includes(query) ||
        experiment.task.toLowerCase().includes(query),
    );
  }, [data, search]);

  return (
    <div className="mx-auto w-full max-w-6xl flex-1 px-4 py-8 sm:px-6 sm:py-12">
      <div className="flex flex-col gap-6 border-b border-border pb-8 md:flex-row md:items-end md:justify-between">
        <div className="max-w-2xl">
          <p className="mb-3 font-mono text-xs font-semibold uppercase tracking-[0.16em] text-accent">
            Evaluation workspace
          </p>
          <h1 className="text-3xl font-semibold tracking-tight sm:text-4xl">Experiments</h1>
          <p className="mt-3 max-w-xl text-sm leading-6 text-fg-muted sm:text-base">
            Run an agent, diagnose the failure, validate a policy change, and adopt only what improves the evidence.
          </p>
        </div>
        <Link
          to="/experiments/new"
          className="inline-flex w-fit items-center gap-2 rounded-md bg-accent px-4 py-2.5 text-sm font-semibold text-white shadow-inner-glow-accent transition-colors hover:bg-accent/90"
        >
          <Plus className="h-4 w-4" weight="bold" />
          New experiment
        </Link>
      </div>

      <div className="mt-8">
        <label className="relative block max-w-md">
          <span className="sr-only">Search experiments</span>
          <MagnifyingGlass
            className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-fg-muted"
            aria-hidden="true"
          />
          <input
            type="search"
            value={search}
            onChange={(event) => setSearch(event.target.value)}
            placeholder="Search experiments"
            className="w-full rounded-md border border-border bg-bg-card py-2.5 pl-10 pr-4 text-sm text-fg-primary shadow-inner-glow placeholder:text-fg-subtle focus:border-accent/50"
          />
        </label>
      </div>

      <div className="mt-6">
        {isLoading ? <LoadingSkeleton variant="cards" /> : null}
        {error ? (
          <ErrorBanner message={getApiErrorMessage(error)} onRetry={() => void refetch()} />
        ) : null}
        {!isLoading && !error && experiments.length === 0 ? (
          search ? (
            <EmptyState
              icon={MagnifyingGlass}
              message="No matching experiments"
              description="Try a different name or task description."
              actionLabel="Clear search"
              onAction={() => setSearch("")}
            />
          ) : (
            <EmptyState
              icon={Flask}
              message="No experiments yet"
              description="Create the checkout latency scenario to walk through the complete evaluation loop."
              actionLabel="Create experiment"
              onAction={() => window.location.assign("/experiments/new")}
            />
          )
        ) : null}

        {!isLoading && !error && experiments.length > 0 ? (
          <div className="grid gap-3">
            {experiments.map((experiment) => {
              const latestRun = [...experiment.runs].sort(
                (a, b) => new Date(b.queued_at).getTime() - new Date(a.queued_at).getTime(),
              )[0];
              return (
                <Link
                  key={experiment.id}
                  to={`/experiments/${experiment.id}`}
                  className="group grid gap-5 rounded-md border border-border bg-bg-card p-5 shadow-inner-glow transition-colors hover:border-border-strong hover:bg-bg-card-raised md:grid-cols-[minmax(0,1fr)_auto_auto_auto] md:items-center"
                >
                  <div className="min-w-0">
                    <div className="flex flex-wrap items-center gap-2">
                      <h2 className="truncate text-base font-semibold text-fg-primary group-hover:text-accent">
                        {experiment.name}
                      </h2>
                      {experiment.active_policy ? <LifecycleBadge status="active" /> : null}
                    </div>
                    <p className="mt-1 line-clamp-2 text-sm leading-5 text-fg-muted">
                      {experiment.task}
                    </p>
                  </div>
                  <div className="flex items-center justify-between gap-8 md:block md:min-w-24">
                    <span className="font-mono text-[10px] uppercase tracking-wider text-fg-subtle">Latest run</span>
                    <div className="mt-1">{latestRun ? <LifecycleBadge status={latestRun.status} /> : <span className="text-sm text-fg-muted">Not run</span>}</div>
                  </div>
                  <div className="flex items-center justify-between gap-8 md:block md:min-w-16">
                    <span className="font-mono text-[10px] uppercase tracking-wider text-fg-subtle">Score</span>
                    <p className="mt-1 font-mono text-sm text-fg-primary">{formatScore(latestRun?.score ?? null)}</p>
                  </div>
                  <div className="flex items-center justify-between gap-4 md:min-w-32">
                    <span className="text-xs text-fg-muted">{formatDate(latestRun?.queued_at ?? experiment.created_at)}</span>
                    <ArrowRight className="h-4 w-4 text-fg-subtle transition-transform group-hover:translate-x-1 group-hover:text-accent" />
                  </div>
                </Link>
              );
            })}
          </div>
        ) : null}
      </div>
    </div>
  );
}
