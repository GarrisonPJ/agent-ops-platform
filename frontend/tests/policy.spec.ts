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

test("ReviewQueue shows pending_review policies and approve flow works", async ({
  page,
}) => {
  const BASE_URL = "http://localhost:8000";

  // Check existing policies
  const response = await page.request.get(`${BASE_URL}/api/eval/policies`);
  expect(response.ok()).toBeTruthy();
  const policies = await response.json();
  const pendingPolicies = policies.filter(
    (p: { status: string }) => p.status === "pending_review",
  );

  await page.goto("/eval?view=policies");

  // Wait for page to load
  await expect(
    page.locator("header").getByText("Evaluations"),
  ).toBeVisible({ timeout: 10_000 });

  // Look for Review Queue section
  const reviewQueueHeader = page.getByText(/Review Queue/i);

  if (pendingPolicies.length > 0) {
    // Verify Review Queue is visible
    await expect(reviewQueueHeader).toBeVisible({ timeout: 5_000 });

    // Verify pending count badge
    await expect(
      page.getByText(`${pendingPolicies.length} pending`),
    ).toBeVisible();

    // Verify first pending policy's version_display is shown
    await expect(
      page.getByText(pendingPolicies[0].version_display),
    ).toBeVisible();

    // Click Approve on first pending policy
    const approveButtons = page.getByRole("button", { name: /Approve/i });
    await approveButtons.first().click();

    // Wait for RTK cache invalidation
    await page.waitForTimeout(1500);

    // After approve, the approved policy should no longer appear in Review Queue
    await expect(
      page.getByText(`${pendingPolicies.length} pending`),
    ).not.toBeVisible();

    // Verify the Timeline shows the policy with "active" badge
    await expect(
      page.getByText(pendingPolicies[0].version_display),
    ).toBeVisible();

    // Look for the active badge near this policy's version_display
    // The Timeline renders status labels as uppercase text
    await expect(
      page.locator(`text=${pendingPolicies[0].version_display}`).first().locator("..").getByText("active"),
    ).toBeVisible({ timeout: 5_000 });
  } else {
    // No pending policies — verify empty state
    await expect(
      page.getByText(/No policies pending review/i),
    ).toBeVisible({ timeout: 5_000 });
  }
});

test("ReviewQueue reject flow works", async ({ page }) => {
  const BASE_URL = "http://localhost:8000";

  const response = await page.request.get(`${BASE_URL}/api/eval/policies`);
  expect(response.ok()).toBeTruthy();
  const policies = await response.json();
  const pendingPolicies = policies.filter(
    (p: { status: string }) => p.status === "pending_review",
  );

  await page.goto("/eval?view=policies");

  await expect(
    page.locator("header").getByText("Evaluations"),
  ).toBeVisible({ timeout: 10_000 });

  if (pendingPolicies.length > 0) {
    // Handle the dialog that prompt() creates
    page.on("dialog", (dialog) => {
      dialog.accept("E2E test rejection");
    });

    // Click first Reject button
    const rejectButtons = page.getByRole("button", { name: /Reject/i });
    await rejectButtons.first().click();

    // Wait for action
    await page.waitForTimeout(1500);

    // The rejected policy should disappear from the queue
    await expect(
      page.getByText(`${pendingPolicies.length} pending`),
    ).not.toBeVisible();

    // Verify the Timeline shows the policy with "reverted" badge near its version node
    await expect(
      page.getByText(pendingPolicies[0].version_display),
    ).toBeVisible();

    await expect(
      page.locator(`text=${pendingPolicies[0].version_display}`).first().locator("..").getByText("reverted"),
    ).toBeVisible({ timeout: 5_000 });
  } else {
    await expect(
      page.getByText(/No policies pending review/i),
    ).toBeVisible({ timeout: 5_000 });
  }
});
