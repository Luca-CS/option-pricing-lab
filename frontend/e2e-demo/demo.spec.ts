// The public demo, as a visitor meets it: the first page and its three profiles, each story walked from its first step
// to its last (its jobs replayed), and a request the recording does not hold refused as read-only. No console error,
// no serious accessibility violation, on any step.

import { accessible, expect, type Page, test } from "../e2e/fixtures";
import { STORIES } from "../src/demo/stories";

async function landing(page: Page): Promise<void> {
  await page.goto("./");
  await expect(page.getByRole("heading", { name: /An equity derivatives desk/ })).toBeVisible();
}

/** No screen of the step says its data is missing from the recording. */
async function complete(page: Page): Promise<void> {
  await page.waitForLoadState("networkidle");
  await expect(page.getByText("Not in the demo", { exact: false })).toHaveCount(0);
  await expect(page.getByText(/cannot be read/)).toHaveCount(0);
}

test("the first page offers the three profiles and says what the numbers are", async ({ page }) => {
  await landing(page);
  for (const story of STORIES) {
    await expect(page.getByRole("heading", { name: story.label, exact: true })).toBeVisible();
  }
  await expect(page.getByText(/Every number here is made up/)).toBeVisible();
  await accessible(page);
});

for (const story of STORIES) {
  test(`the ${story.label.toLowerCase()}'s story runs from its first step to its last`, async ({ page }) => {
    test.setTimeout(180_000);
    await landing(page);
    await page.getByRole("button", { name: `Follow the ${story.label.toLowerCase()}'s story` }).click();
    const panel = page.getByRole("complementary", { name: `The ${story.label.toLowerCase()}'s story` });
    await expect(page.getByRole("note", { name: "Demo" })).toContainText("synthetic data");
    for (const [i, step] of story.steps.entries()) {
      await expect(panel.getByRole("heading", { name: step.title })).toBeVisible();
      await expect(panel).toContainText(`${i + 1} of ${story.steps.length}`);
      if (story.profile === "structurer" && i === 0) {
        // the story's own quote, replayed: its progress, then its result
        await page.getByRole("button", { name: "Quote", exact: true }).click();
        await expect(page.getByText(/Coupon solved for a note price of/)).toBeVisible({ timeout: 20_000 });
      }
      if (story.profile === "quant" && step.to === "/quant/vollab") {
        await page.getByRole("button", { name: "Fit the models" }).click();
        await expect(page.getByRole("table", { name: "Each model's error against the quotes" })).toBeVisible({
          timeout: 20_000,
        });
      }
      await complete(page);
      await accessible(page);
      if (i < story.steps.length - 1) await panel.getByRole("button", { name: "Next" }).click();
    }
    await panel.getByRole("button", { name: "Done: look around" }).click();
    await expect(panel).toBeHidden();
  });
}

test("the trader's book filters and groups its trades, and stays responsive", async ({ page }) => {
  await landing(page);
  await page.getByRole("button", { name: "Look around freely" }).nth(1).click();
  await page.getByRole("link", { name: "Book", exact: true }).click();
  await expect(page.getByRole("button", { name: /^SPX\s*\d+ rows/ })).toBeVisible();
  await expect(page.getByRole("link", { name: "DEMO-01", exact: true })).toHaveCount(0); // called: not live
  await page.getByText("All", { exact: true }).click();
  await expect(page.getByRole("link", { name: "DEMO-01", exact: true })).toBeVisible();
  await page.getByRole("combobox", { name: "Group by" }).click();
  await page.getByRole("option", { name: "By status" }).click();
  await expect(page.getByRole("button", { name: /^called\s*\d+ rows?/ })).toBeVisible();
  await page.getByText("Standard", { exact: true }).click();
  await expect(page.getByRole("radio", { name: "Standard" })).toBeChecked();
  // the book's quick valuation is recorded: it replays, with its progress, then its simulations
  await page.getByText("Quick", { exact: true }).click();
  await page.getByRole("button", { name: "Value the book" }).click();
  await expect(page.getByRole("heading", { name: "Simulations" })).toBeVisible({ timeout: 20_000 });
  await accessible(page);
});

test("on a phone, the story's card leaves the page's last rows within reach", async ({ page }) => {
  await page.setViewportSize({ width: 375, height: 812 });
  await landing(page);
  await page.getByRole("button", { name: "Follow the trader's story" }).click();
  await expect(page.getByRole("complementary", { name: "The trader's story" })).toBeVisible();
  await page.goto("trading/book");
  const last = page.getByRole("link", { name: "DEMO-04", exact: true });
  await expect(last).toBeAttached();
  await page.evaluate(() => window.scrollTo(0, document.documentElement.scrollHeight));
  // at the foot of the page, the last trade is above the card, not under it
  const onTop = await last.evaluate((el) => {
    const b = el.getBoundingClientRect();
    const hit = document.elementFromPoint(b.left + b.width / 2, b.top + b.height / 2);
    return hit !== null && (el === hit || el.contains(hit));
  });
  expect(onTop).toBe(true);
});

test("a request the recording does not hold says the demo is read-only", async ({ page }) => {
  await landing(page);
  await page.getByRole("button", { name: "Look around freely" }).first().click();
  await page.goto("structuring/builder?preset=phoenix");
  const knockIn = page.getByRole("textbox", { name: "Knock-in barrier" });
  await expect(knockIn).toHaveValue("60");
  await knockIn.fill("55");
  await knockIn.press("Enter");
  await page.getByRole("button", { name: "Quote", exact: true }).click();
  await expect(page.getByText("The demo is read-only").first()).toBeVisible();
});
