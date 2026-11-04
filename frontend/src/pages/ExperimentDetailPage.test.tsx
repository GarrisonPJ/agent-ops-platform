import { render, screen, within } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import { baselineRun, cloneFixture, goldenExperiment } from "../services/recorded/fixtures";
import type { Experiment, TrialGroupSummary } from "../types/phase1";
import ExperimentDetailPage from "./ExperimentDetailPage";

const mocks = vi.hoisted(() => ({ experiment: undefined as unknown }));

vi.mock("../services/experimentsApi", () => ({
  useGetExperimentQuery: () => ({
    data: mocks.experiment,
    error: undefined,
    isLoading: false,
    refetch: vi.fn(),
  }),
  useCreateBaselineRunMutation: () => [vi.fn(), { isLoading: false }],
}));

function renderPage(experiment: Experiment) {
  mocks.experiment = experiment;
  return render(
    <MemoryRouter initialEntries={[`/experiments/${experiment.id}`]}>
      <Routes>
        <Route path="/experiments/:id" element={<ExperimentDetailPage />} />
      </Routes>
    </MemoryRouter>,
  );
}

function withTrials(
  states: Array<["succeeded" | "failed" | "queued", number]>,
  group: Partial<TrialGroupSummary>,
): Experiment {
  const experiment = cloneFixture(goldenExperiment);
  const groupId = "group-1";
  const runs = states.map(([status, index]) => ({
    ...cloneFixture(baselineRun),
    id: `trial-${index}`,
    status,
    trial_group_id: groupId,
    trial_index: index,
  }));
  experiment.runs = [...runs, ...experiment.runs];
  experiment.trial_groups = [
    {
      id: groupId,
      experiment_id: experiment.id,
      policy_id: null,
      n: runs.length,
      k: runs.length,
      seed: 42,
      created_at: "2026-11-03T10:00:00Z",
      run_ids: runs.map((run) => run.id),
      state: "complete",
      terminal: runs.length,
      successes: null,
      pass_at_1: null,
      pass_at_k: null,
      mean_score: null,
      mean_steps: null,
      ...group,
    },
  ];
  return experiment;
}

afterEach(() => {
  mocks.experiment = undefined;
});

describe("ExperimentDetailPage trial groups", () => {
  it("shows no trial section for an Experiment without groups", () => {
    const experiment = cloneFixture(goldenExperiment);
    expect(experiment.trial_groups).toEqual([]);
    renderPage(experiment);
    expect(screen.getByRole("heading", { name: "Runs" })).toBeTruthy();
    expect(screen.queryByRole("heading", { name: "Trial groups" })).toBeNull();
    expect(screen.queryByText(/^Trial \d+$/)).toBeNull();
  });

  it("shows pass^1, pass^k and every trial's status for a published group", () => {
    renderPage(
      withTrials(
        [
          ["succeeded", 1],
          ["failed", 2],
          ["succeeded", 3],
          ["succeeded", 4],
          ["failed", 5],
        ],
        {
          k: 2,
          successes: 3,
          pass_at_1: 0.6,
          pass_at_k: 0.3,
          mean_score: 0.57,
          mean_steps: 4.2,
        },
      ),
    );
    const group = screen.getByRole("article", { name: "Trial group group-1" });
    expect(within(group).getByText("Baseline trials · n=5, k=2")).toBeTruthy();
    expect(within(group).getByText("3/5 trials succeeded.")).toBeTruthy();
    expect(within(group).getByText("pass^1").nextSibling?.textContent).toBe("60.0%");
    expect(within(group).getByText("pass^2").nextSibling?.textContent).toBe("30.0%");
    expect(within(group).getByText("Mean score").nextSibling?.textContent).toBe("0.57");
    expect(within(group).getByText("Mean steps").nextSibling?.textContent).toBe("4.2");
    const trials = within(within(group).getByRole("list", { name: "Trials" })).getAllByRole("link");
    expect(trials.map((link) => link.textContent)).toEqual([
      "#1Succeeded",
      "#2Failed",
      "#3Succeeded",
      "#4Succeeded",
      "#5Failed",
    ]);
    expect(trials[1].getAttribute("href")).toBe("/runs/trial-2");
    expect(screen.getByText("Trial 3")).toBeTruthy();
  });

  it("does not show pass^k for a running or cancelled group", () => {
    const { unmount } = renderPage(
      withTrials(
        [
          ["succeeded", 1],
          ["queued", 2],
        ],
        { state: "running", terminal: 1 },
      ),
    );
    const running = screen.getByRole("article", { name: "Trial group group-1" });
    expect(
      within(running).getByText("1/2 trials finished; pass^k is published when all finish."),
    ).toBeTruthy();
    expect(within(running).getByText("pass^2").nextSibling?.textContent).toBe("—");
    unmount();

    renderPage(
      withTrials(
        [
          ["succeeded", 1],
          ["failed", 2],
        ],
        { state: "cancelled", policy_id: "policy-1" },
      ),
    );
    const cancelled = screen.getByRole("article", { name: "Trial group group-1" });
    expect(within(cancelled).getByText("Candidate policy replay · n=2, k=2")).toBeTruthy();
    expect(
      within(cancelled).getByText(
        "Not published: a trial was cancelled, so the group has no pass^k.",
      ),
    ).toBeTruthy();
  });
});
