import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import NewExperimentPage from "./NewExperimentPage";


const mocks = vi.hoisted(() => ({
  createExperiment: vi.fn(),
  navigate: vi.fn(),
  scenarios: [
    {
      id: "checkout-api-latency.v1",
      name: "Checkout API Latency",
      description: "Investigate elevated checkout latency.",
      default_task:
        "Investigate why the checkout API latency increased after the latest deployment.",
      allowed_tools: [
        "check_service_health",
        "query_service_metrics",
        "fetch_service_logs",
      ],
      params: [],
    },
    {
      id: "multi-step-research.v1",
      name: "Multi-step Research",
      description: "Search, fetch, and answer.",
      default_task: "Answer a research question using search and fetch tools",
      allowed_tools: ["search_documents", "fetch_document", "submit_answer"],
      params: [
        {
          name: "topic",
          description: "Research topic used by search tools.",
          required: true,
        },
      ],
    },
  ],
}));

vi.mock("../services/experimentsApi", () => ({
  useGetScenariosQuery: () => ({
    data: mocks.scenarios,
    isLoading: false,
    error: undefined,
  }),
  useCreateExperimentMutation: () => [mocks.createExperiment, { isLoading: false }],
}));

vi.mock("react-router-dom", async () => {
  const actual = await vi.importActual<typeof import("react-router-dom")>("react-router-dom");
  return { ...actual, useNavigate: () => mocks.navigate };
});


afterEach(() => {
  mocks.createExperiment.mockReset();
  mocks.navigate.mockReset();
});


describe("NewExperimentPage", () => {
  it("submits provider mode without exposing provider configuration fields", async () => {
    mocks.createExperiment.mockReturnValue({
      unwrap: vi.fn().mockResolvedValue({ id: "provider-experiment" }),
    });
    render(
      <MemoryRouter>
        <NewExperimentPage />
      </MemoryRouter>,
    );

    expect(screen.getByRole("radio", { name: /^Fixture/ })).toBeTruthy();
    const provider = screen.getByRole("radio", {
      name: /^OpenAI-compatible provider/,
    });
    fireEvent.click(provider);
    expect(provider).toHaveProperty("checked", true);
    expect(screen.queryByLabelText(/^API key$/i)).toBeNull();
    expect(screen.queryByLabelText(/^base URL$/i)).toBeNull();
    expect(screen.queryByLabelText(/^model$/i)).toBeNull();

    const form = screen.getByRole("button", { name: /Create experiment/i }).closest("form");
    expect(form).not.toBeNull();
    fireEvent.submit(form!);

    await waitFor(() =>
      expect(mocks.createExperiment).toHaveBeenCalledWith({
        name: "Checkout latency investigation",
        task: "Investigate why the checkout API latency increased after the latest deployment.",
        scenario_id: "checkout-api-latency.v1",
        execution_mode: "provider",
      }),
    );
    expect(mocks.navigate).toHaveBeenCalledWith("/experiments/provider-experiment");
  });

  it("lists registered scenarios from the API", () => {
    render(
      <MemoryRouter>
        <NewExperimentPage />
      </MemoryRouter>,
    );

    expect(screen.getByRole("radio", { name: /Checkout API Latency/ })).toBeTruthy();
    expect(screen.getByRole("radio", { name: /Multi-step Research/ })).toBeTruthy();
    expect(screen.getByText(/check_service_health/)).toBeTruthy();
  });

  it("requires scenario params before submitting research experiments", async () => {
    mocks.createExperiment.mockReturnValue({
      unwrap: vi.fn().mockResolvedValue({ id: "research-experiment" }),
    });
    render(
      <MemoryRouter>
        <NewExperimentPage />
      </MemoryRouter>,
    );

    fireEvent.click(screen.getByRole("radio", { name: /Multi-step Research/ }));
    const form = screen.getByRole("button", { name: /Create experiment/i }).closest("form");
    expect(form).not.toBeNull();
    fireEvent.submit(form!);

    expect(await screen.findByText(/Scenario parameter "topic" is required/)).toBeTruthy();
    expect(mocks.createExperiment).not.toHaveBeenCalled();
  });

  it("submits scenario params for research experiments", async () => {
    mocks.createExperiment.mockReturnValue({
      unwrap: vi.fn().mockResolvedValue({ id: "research-experiment" }),
    });
    render(
      <MemoryRouter>
        <NewExperimentPage />
      </MemoryRouter>,
    );

    fireEvent.click(screen.getByRole("radio", { name: /Multi-step Research/ }));
    fireEvent.change(screen.getByLabelText(/^topic/i), {
      target: { value: "payment latency" },
    });
    const form = screen.getByRole("button", { name: /Create experiment/i }).closest("form");
    expect(form).not.toBeNull();
    fireEvent.submit(form!);

    await waitFor(() =>
      expect(mocks.createExperiment).toHaveBeenCalledWith({
        name: "Multi-step Research investigation",
        task: "Answer a research question using search and fetch tools",
        scenario_id: "multi-step-research.v1",
        execution_mode: "fixture",
        scenario_params: { topic: "payment latency" },
      }),
    );
  });
});
