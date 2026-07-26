import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import NewExperimentPage from "./NewExperimentPage";


const mocks = vi.hoisted(() => ({
  createExperiment: vi.fn(),
  navigate: vi.fn(),
}));

vi.mock("../services/experimentsApi", () => ({
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
        scenario_id: "checkout-api-latency",
        execution_mode: "provider",
      }),
    );
    expect(mocks.navigate).toHaveBeenCalledWith("/experiments/provider-experiment");
  });
});
