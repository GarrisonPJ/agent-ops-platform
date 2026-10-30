import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import NewExperimentPage from "./NewExperimentPage";

const mocks = vi.hoisted(() => ({
  createExperiment: vi.fn(),
  navigate: vi.fn(),
  agentsQuery: vi.fn(),
  agents: [
    {
      id: "fixture-agent.v1",
      name: "Deterministic fixture agent",
      kind: "fixture",
      description: "Deterministic MCP agent.",
    },
    {
      id: "reference-openai-compatible.v1",
      name: "OpenAI-compatible reference agent",
      kind: "reference",
      description: "Runner-side provider configuration.",
    },
  ],
  scenarios: [
    {
      id: "checkout-api-latency.v1",
      name: "Checkout API Latency",
      description: "Investigate elevated checkout latency.",
      default_task:
        "Investigate why the checkout API latency increased after the latest deployment.",
      allowed_tools: ["check_service_health", "query_service_metrics", "fetch_service_logs"],
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
  useGetAgentsQuery: () =>
    mocks.agentsQuery() ?? { data: mocks.agents, isLoading: false, error: undefined },
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
  mocks.agentsQuery.mockReset();
});

describe("NewExperimentPage", () => {
  it("loads registered Agents and defaults to automatic fixture selection", async () => {
    mocks.agentsQuery.mockReturnValue({ isLoading: true });
    const { rerender } = render(
      <MemoryRouter>
        <NewExperimentPage />
      </MemoryRouter>,
    );
    expect(screen.getByRole("combobox", { name: "Agent" })).toHaveProperty("disabled", true);
    expect(screen.getByRole("status").textContent).toContain("Loading registered Agents");
    mocks.agentsQuery.mockReturnValue({ data: mocks.agents, isLoading: false });
    rerender(
      <MemoryRouter>
        <NewExperimentPage />
      </MemoryRouter>,
    );
    expect(screen.getByRole("combobox", { name: "Agent" })).toHaveProperty("value", "");
    expect(screen.getAllByRole("option")).toHaveLength(3);
    expect(screen.getByText(/Deterministic MCP agent/)).toBeTruthy();
    expect(screen.getByText(/Automatic omits agent_id/).textContent).toContain(
      "reference-openai-compatible.v1",
    );
    mocks.createExperiment.mockReturnValue({
      unwrap: vi.fn().mockResolvedValue({ id: "fixture" }),
    });
    fireEvent.click(screen.getByRole("button", { name: /Create experiment/i }));
    await waitFor(() => expect(mocks.createExperiment).toHaveBeenCalled());
    expect(mocks.createExperiment.mock.calls[0][0]).toMatchObject({ execution_mode: "fixture" });
    expect(mocks.createExperiment.mock.calls[0][0]).not.toHaveProperty("agent_id");
  });

  it.each(["fixture-agent.v1", "reference-openai-compatible.v1"])(
    "preserves explicit %s across mode toggles and submits it even when mode differs",
    async (agentId) => {
      mocks.createExperiment.mockReturnValue({
        unwrap: vi.fn().mockResolvedValue({ id: "explicit" }),
      });
      render(
        <MemoryRouter>
          <NewExperimentPage />
        </MemoryRouter>,
      );
      const select = screen.getByRole("combobox", { name: "Agent" });
      fireEvent.change(select, { target: { value: agentId } });
      fireEvent.click(screen.getByRole("radio", { name: /^OpenAI-compatible provider/ }));
      fireEvent.click(screen.getByRole("radio", { name: /^Fixture/ }));
      if (agentId === "fixture-agent.v1") {
        fireEvent.click(screen.getByRole("radio", { name: /^OpenAI-compatible provider/ }));
      }
      expect(select).toHaveProperty("value", agentId);
      fireEvent.click(screen.getByRole("button", { name: /Create experiment/i }));
      await waitFor(() => expect(mocks.createExperiment).toHaveBeenCalled());
      expect(mocks.createExperiment.mock.calls[0][0]).toMatchObject({
        agent_id: agentId,
        execution_mode: agentId === "fixture-agent.v1" ? "provider" : "fixture",
      });
    },
  );

  it.each([
    { data: undefined, isLoading: true, error: undefined, message: /Loading registered Agents/ },
    { data: [], isLoading: false, error: undefined, message: /No registered Agents are available/ },
    { data: mocks.agents, isLoading: false, error: { status: 503 }, message: /Agents unavailable/ },
  ])("fails closed when the registry is unavailable: $message", (query) => {
    mocks.agentsQuery.mockReturnValue(query);
    render(
      <MemoryRouter>
        <NewExperimentPage />
      </MemoryRouter>,
    );
    expect(screen.getByText(query.message)).toBeTruthy();
    expect(screen.getByRole("combobox", { name: "Agent" })).toHaveProperty("disabled", true);
    expect(screen.getAllByRole("option")).toHaveLength(1);
    const button = screen.getByRole("button", { name: /Create experiment/i });
    expect(button).toHaveProperty("disabled", true);
    fireEvent.submit(button.closest("form")!);
    expect(mocks.createExperiment).not.toHaveBeenCalled();
  });

  it("does not silently fall back when an explicit Agent is removed from the registry", () => {
    const { rerender } = render(
      <MemoryRouter>
        <NewExperimentPage />
      </MemoryRouter>,
    );
    fireEvent.change(screen.getByRole("combobox", { name: "Agent" }), {
      target: { value: "reference-openai-compatible.v1" },
    });
    mocks.agentsQuery.mockReturnValue({ data: [mocks.agents[0]], isLoading: false });
    rerender(
      <MemoryRouter>
        <NewExperimentPage />
      </MemoryRouter>,
    );
    expect(screen.getByText(/selected Agent or mode default is not registered/)).toBeTruthy();
    const button = screen.getByRole("button", { name: /Create experiment/i });
    expect(button).toHaveProperty("disabled", true);
    fireEvent.submit(button.closest("form")!);
    expect(mocks.createExperiment).not.toHaveBeenCalled();
  });

  it("requires a registered mode default but allows explicit selection instead", () => {
    mocks.agentsQuery.mockReturnValue({ data: [mocks.agents[0]], isLoading: false });
    render(
      <MemoryRouter>
        <NewExperimentPage />
      </MemoryRouter>,
    );
    fireEvent.click(screen.getByRole("radio", { name: /^OpenAI-compatible provider/ }));
    expect(screen.getByRole("button", { name: /Create experiment/i })).toHaveProperty(
      "disabled",
      true,
    );
    fireEvent.change(screen.getByRole("combobox", { name: "Agent" }), {
      target: { value: "fixture-agent.v1" },
    });
    expect(screen.getByRole("button", { name: /Create experiment/i })).toHaveProperty(
      "disabled",
      false,
    );
  });

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
    expect(screen.queryByLabelText(/^command$/i)).toBeNull();

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
    expect(mocks.createExperiment.mock.calls[0][0]).not.toHaveProperty("agent_id");
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
