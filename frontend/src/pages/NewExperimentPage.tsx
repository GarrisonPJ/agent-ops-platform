import { ArrowLeft, ArrowRight, CheckCircle, Pulse } from "@phosphor-icons/react";
import { FormEvent, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import ErrorBanner from "../components/ErrorBanner";
import { getApiErrorMessage } from "../lib/phase1Format";
import { useCreateExperimentMutation } from "../services/experimentsApi";

const DEFAULT_TASK =
  "Investigate why the checkout API latency increased after the latest deployment.";

export default function NewExperimentPage() {
  const navigate = useNavigate();
  const [name, setName] = useState("Checkout latency investigation");
  const [task, setTask] = useState(DEFAULT_TASK);
  const [formError, setFormError] = useState<string | null>(null);
  const [createExperiment, { isLoading }] = useCreateExperimentMutation();

  const submit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    setFormError(null);
    if (!name.trim() || !task.trim()) {
      setFormError("Name and task are required.");
      return;
    }
    try {
      const experiment = await createExperiment({
        name: name.trim(),
        task: task.trim(),
        scenario_id: "checkout-api-latency",
      }).unwrap();
      navigate(`/experiments/${experiment.id}`);
    } catch (error) {
      setFormError(getApiErrorMessage(error));
    }
  };

  return (
    <div className="mx-auto w-full max-w-3xl flex-1 px-4 py-8 sm:px-6 sm:py-12">
      <Link
        to="/experiments"
        className="mb-8 inline-flex items-center gap-2 rounded-md text-sm text-fg-muted transition-colors hover:text-fg-primary"
      >
        <ArrowLeft className="h-4 w-4" />
        Experiments
      </Link>

      <p className="font-mono text-xs font-semibold uppercase tracking-[0.16em] text-accent">Golden scenario</p>
      <h1 className="mt-3 text-3xl font-semibold tracking-tight sm:text-4xl">Create an experiment</h1>
      <p className="mt-3 max-w-2xl text-sm leading-6 text-fg-muted sm:text-base">
        Start with a deterministic checkout incident designed to demonstrate the full baseline-to-policy loop.
      </p>

      <form onSubmit={submit} className="mt-10 space-y-6" noValidate>
        {formError ? <ErrorBanner title="Experiment not created" message={formError} /> : null}

        <fieldset className="rounded-md border border-accent/25 bg-accent/[0.06] p-5 shadow-inner-glow">
          <legend className="px-2 font-mono text-xs font-semibold uppercase tracking-wider text-accent">
            Scenario
          </legend>
          <div className="flex items-start gap-4">
            <span className="flex h-10 w-10 shrink-0 items-center justify-center rounded-md border border-accent/20 bg-accent/10 text-accent">
              <Pulse className="h-5 w-5" weight="bold" />
            </span>
            <div>
              <div className="flex flex-wrap items-center gap-2">
                <h2 className="font-semibold">Checkout API latency</h2>
                <span className="rounded-full border border-success/20 bg-success/10 px-2 py-0.5 font-mono text-[10px] font-semibold text-success">
                  Deterministic
                </span>
              </div>
              <p className="mt-2 text-sm leading-6 text-fg-muted">
                The baseline repeats log queries until its budget is exhausted. The candidate policy establishes health and metrics before targeted logs.
              </p>
              <ul className="mt-3 grid gap-2 text-xs text-fg-muted sm:grid-cols-3">
                {["check_service_health", "query_service_metrics", "fetch_service_logs"].map((tool) => (
                  <li key={tool} className="flex items-center gap-1.5 font-mono">
                    <CheckCircle className="h-3.5 w-3.5 text-success" weight="fill" />
                    {tool}
                  </li>
                ))}
              </ul>
            </div>
          </div>
        </fieldset>

        <label className="block">
          <span className="text-sm font-semibold">Experiment name</span>
          <input
            value={name}
            onChange={(event) => setName(event.target.value)}
            maxLength={120}
            autoFocus
            className="mt-2 w-full rounded-md border border-border bg-bg-card px-4 py-3 text-sm text-fg-primary shadow-inner-glow placeholder:text-fg-subtle focus:border-accent/50"
          />
        </label>

        <label className="block">
          <span className="text-sm font-semibold">Task</span>
          <span className="ml-2 text-xs text-fg-muted">What should the agent investigate?</span>
          <textarea
            value={task}
            onChange={(event) => setTask(event.target.value)}
            rows={5}
            maxLength={2000}
            className="mt-2 w-full resize-y rounded-md border border-border bg-bg-card px-4 py-3 text-sm leading-6 text-fg-primary shadow-inner-glow placeholder:text-fg-subtle focus:border-accent/50"
          />
        </label>

        <div className="flex flex-col-reverse gap-3 border-t border-border pt-6 sm:flex-row sm:justify-end">
          <Link
            to="/experiments"
            className="rounded-md border border-border px-4 py-2.5 text-center text-sm font-semibold text-fg-muted transition-colors hover:bg-white/[0.04] hover:text-fg-primary"
          >
            Cancel
          </Link>
          <button
            type="submit"
            disabled={isLoading}
            className="inline-flex items-center justify-center gap-2 rounded-md bg-accent px-5 py-2.5 text-sm font-semibold text-white shadow-inner-glow-accent transition-colors hover:bg-accent/90 disabled:cursor-wait disabled:opacity-60"
          >
            {isLoading ? "Creating…" : "Create experiment"}
            {!isLoading ? <ArrowRight className="h-4 w-4" weight="bold" /> : null}
          </button>
        </div>
      </form>
    </div>
  );
}
