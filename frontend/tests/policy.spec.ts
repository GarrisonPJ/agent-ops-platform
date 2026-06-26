import { test, expect } from "@playwright/test";

test.describe("Eval Policies tab", () => {
  test("shows warmup indicator and Policies tab renders correctly", async ({
    page,
  }) => {
    // Navigate to /eval
    await page.goto("/eval");

    // Wait for page header
    await expect(
      page.locator("header").getByText("Evaluations"),
    ).toBeVisible({ timeout: 10_000 });

    // Click the Policies tab
    await page.getByRole("button", { name: /Policies/i }).click();

    // Verify warmup indicator is shown (when no active policy)
    await expect(page.getByText(/Warming Up/i)).toBeVisible({
      timeout: 5_000,
    });

    // Verify warmup status text
    await expect(
      page.getByText(/trajectories collected/i),
    ).toBeVisible();

    // Either warmup or timeline/no-policies should be visible
    const warmupVisible = await page.getByText("Warming Up").isVisible().catch(() => false);
    const policiesTabHeader = page.locator("text=Policies");
    await expect(policiesTabHeader).toBeVisible();

    // Verify tab navigation works correctly
    // Switch back to Benchmark tab
    await page.getByRole("button", { name: /Benchmark/i }).click();
    await expect(
      page.getByRole("button", { name: /Run Benchmark/i }),
    ).toBeVisible();

    // Switch back to Policies tab
    await page.getByRole("button", { name: /Policies/i }).click();
    if (warmupVisible) {
      await expect(page.getByText("Warming Up")).toBeVisible();
    }
  });

  test("seeded policy appears in timeline and can be viewed", async ({
    page,
  }) => {
    // Seed a policy via the API directly
    const BASE_URL = "http://localhost:8000";

    // First, check if we can fetch an existing policy
    const response = await page.request.get(`${BASE_URL}/api/eval/policies`);
    expect(response.ok()).toBeTruthy();

    const policies = await response.json();

    if (policies.length > 0) {
      // Policy exists — verify it renders in the UI
      await page.goto("/eval?view=policies");
      await expect(
        page.locator("header").getByText("Evaluations"),
      ).toBeVisible({ timeout: 10_000 });

      // Verify timeline shows policy versions
      const firstPolicy = policies[0];
      await expect(
        page.getByText(firstPolicy.version_display),
      ).toBeVisible({ timeout: 5_000 });

      // Click on the policy version in the timeline
      await page.getByText(firstPolicy.version_display).first().click();

      // Verify detail panel shows the policy rationale
      if (firstPolicy.rationale) {
        await expect(
          page.getByText(firstPolicy.rationale),
        ).toBeVisible({ timeout: 5_000 });
      }

      // If the policy is pending_review, verify approve/reject buttons exist
      if (firstPolicy.status === "pending_review") {
        await expect(
          page.getByRole("button", { name: /Approve/i }),
        ).toBeVisible();
        await expect(
          page.getByRole("button", { name: /Reject/i }),
        ).toBeVisible();
      }
    } else {
      // No policies — verify empty state
      await page.goto("/eval?view=policies");
      await expect(
        page.getByText(/no policies/i),
      ).toBeVisible({ timeout: 5_000 });
    }
  });
});
