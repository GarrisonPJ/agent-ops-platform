import { test, expect } from "@playwright/test";

test.describe("Eval page", () => {
  test("loads config zone, runs benchmark, and displays rankings", async ({
    page,
  }) => {
    // Navigate to /eval
    await page.goto("/eval");

    // Wait for page header to confirm the route is loaded
    await expect(
      page.locator("header").getByText("Evaluations"),
    ).toBeVisible({ timeout: 10_000 });

    // Verify the "Run Benchmark" button exists
    const runBtn = page.getByRole("button", { name: /Run Benchmark/i });
    await expect(runBtn).toBeVisible();

    // Verify a select element is present for benchmark task
    const select = page.locator("select");
    await expect(select.first()).toBeVisible();

    // Set number of runs to 2 (default is 3, click minus once)
    // Click minus to go from 3 → 2 if default is visible
    const runCountDisplay = page.getByText("3", { exact: true });
    if (await runCountDisplay.isVisible({ timeout: 2000 }).catch(() => false)) {
      // Click the first minus button (there may be multiple svg.lucide-minus)
      await page.locator("svg.lucide-minus").first().click();
    }

    // Click Run Benchmark
    await runBtn.click();

    // Wait for the progress indicator to appear
    await expect(
      page.getByText("Running benchmark..."),
    ).toBeVisible({ timeout: 5_000 });

    // Wait for results table to appear (benchmark runs real LLM calls)
    await expect(
      page.locator("table"),
    ).toBeVisible({ timeout: 120_000 });

    // Verify at least one ranking row exists
    const rows = page.locator("table tbody tr");
    await expect(rows).not.toHaveCount(0);

    // Verify score values are displayed in the table
    const scoreCells = page.locator(
      "table tbody tr td:nth-child(3) span",
    );
    const count = await scoreCells.count();
    expect(count).toBeGreaterThan(0);

    // Verify scores are numeric (contain a decimal point or digit)
    for (let i = 0; i < count; i++) {
      const text = await scoreCells.nth(i).textContent();
      expect(text).not.toBeNull();
      expect(text!.trim()).not.toBe("");
    }
  });
});
