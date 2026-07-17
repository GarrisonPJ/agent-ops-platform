import { expect, test } from "@playwright/test";


const BASELINE_RUN_ID = "00000000-0000-4000-8000-000000000102";

test("completes the recorded create-to-activate product loop", async ({
  page,
}) => {
  await page.goto("/experiments/new");
  await expect(page.getByText("Recorded Demo Data")).toBeVisible();

  await page.getByLabel("Experiment name").fill("Browser Golden journey");
  await page
    .getByLabel(/^Task/)
    .fill("Investigate checkout API latency");
  await page.getByRole("button", { name: "Create experiment" }).click();

  await expect(
    page.getByRole("heading", { name: "Browser Golden journey" }),
  ).toBeVisible();
  await page.getByRole("button", { name: "Run baseline" }).first().click();
  await expect(page).toHaveURL(/\/runs\/[^?]+\?view=trace/);
  await expect(page.getByText("Failed", { exact: true })).toBeVisible();
  await expect(
    page
      .getByText(
        "The same noisy request samples are returned; the cause is still inconclusive.",
      )
      .first(),
  ).toBeVisible();

  await page.getByRole("tab", { name: /analysis/i }).click();
  await expect(
    page.getByRole("heading", { name: "planning", exact: true }),
  ).toBeVisible();
  await expect(page.getByText(/Budget exhausted: 6 steps/)).toBeVisible();

  await page.getByRole("tab", { name: /improve/i }).click();
  await expect(
    page.getByText("Candidate policy diff", { exact: true }),
  ).toBeVisible();
  await page
    .getByRole("button", { name: "Run candidate replay" })
    .click();

  await expect(page).toHaveURL(/\/runs\/[^?]+\?view=trace/);
  await expect(page.getByText("Succeeded", { exact: true })).toBeVisible();
  await page.getByRole("tab", { name: /improve/i }).click();
  await expect(page.getByText("Replay comparison")).toBeVisible();
  await expect(page.getByText("Score delta")).toBeVisible();
  await page.getByRole("button", { name: "Activate policy" }).click();
  await expect(
    page.getByText("Policy activated for this experiment."),
  ).toBeVisible();

  await page.getByRole("link", { name: "Experiment", exact: true }).click();
  await expect(page.getByText("Active policy", { exact: true })).toBeVisible();
  await expect(
    page.getByLabel("Policy state").getByText("Active", { exact: true }),
  ).toBeVisible();
});

test("keeps the run workspace usable on mobile and by keyboard", async ({
  page,
}) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto(`/runs/${BASELINE_RUN_ID}?view=trace`);
  await expect(page.getByText("Recorded Demo Data")).toBeVisible();

  const traceTab = page.getByRole("tab", { name: /trace/i });
  const analysisTab = page.getByRole("tab", { name: /analysis/i });
  await traceTab.focus();
  await page.keyboard.press("ArrowRight");
  await expect(analysisTab).toHaveAttribute("aria-selected", "true");
  await expect(page.getByText("Dominant failure")).toBeVisible();

  const overflow = await page.evaluate(
    () => document.documentElement.scrollWidth - window.innerWidth,
  );
  expect(overflow).toBeLessThanOrEqual(1);
});
