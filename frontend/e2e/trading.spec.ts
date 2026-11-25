import { accessible, ensureUsers, expect, signInAs, submitAs, test } from "./fixtures";

test.beforeAll(async ({ baseURL }) => {
  await ensureUsers(baseURL ?? "", ["trader"]);
});

test("the trader values the book and reads it by group", async ({ page }) => {
  test.setTimeout(300_000); // the job may wait behind another in the queue
  await signInAs(page, "trader");
  await page.goto("/trading/book");
  await expect(page.getByRole("button", { name: /^SPX\s*\d+ rows/ })).toBeVisible();
  // the filters live in the URL, and the grid follows them (a grid that kept rendering once held them back for ever)
  await expect(page.getByRole("link", { name: "DEMO-01", exact: true })).toHaveCount(0); // called: not live
  await page.getByText("All", { exact: true }).click();
  await expect(page.getByRole("link", { name: "DEMO-01", exact: true })).toBeVisible();
  await page.getByRole("combobox", { name: "Group by" }).click();
  await page.getByRole("option", { name: "By status" }).click();
  await expect(page.getByRole("button", { name: /^called\s*\d+ rows?/ })).toBeVisible();
  await expect(page).toHaveURL(/show=all/);
  await page.getByRole("button", { name: "Value the book" }).click();
  await expect(page.getByRole("heading", { name: "Simulations" })).toBeVisible({ timeout: 180_000 });
  await expect(page.getByText("Net of the reserve")).toBeVisible();
  await expect(page.getByRole("columnheader", { name: /Unit price/ })).toBeVisible();
  await accessible(page);
});

test("a trade's page holds its sheet, its events and its value today", async ({ page }) => {
  test.setTimeout(300_000);
  await signInAs(page, "trader");
  await page.goto("/trading/trades/DEMO-02");
  await expect(page.getByRole("heading", { name: "The sheet as booked" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Its events" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Versions" })).toBeVisible();
  await page.getByRole("button", { name: "Value at today's market" }).click();
  await expect(page.getByText("Unit price")).toBeVisible({ timeout: 180_000 });
  await accessible(page);
});

test("from the book's Greeks down to a vol node and the trades behind it", async ({ page, baseURL }) => {
  test.setTimeout(600_000);
  // a light run started through the API (few paths, the node vegas and no other variant), so a runner's CPU takes
  // minutes, not tens; the screen follows a running job from its link as if it had started it
  const job = await submitAs(baseURL ?? "", "trader", "/api/v1/risk/greeks", {
    options: {
      precision: "quick",
      n_paths: 2_000,
      kxt: "single",
      curves: false,
      sticky_strike: false,
      realistic: false,
      cross: false,
      theta: false,
    },
  });
  await signInAs(page, "trader");
  await page.goto(`/trading/risk?job=${job}`);
  await expect(page.getByRole("heading", { name: "The book's Greeks" })).toBeVisible({ timeout: 540_000 });
  await page.getByRole("tab", { name: "Vega K × T" }).click();
  const nodes = page.getByRole("button", { name: /at 1y: .* plus or minus/ });
  await nodes.first().click();
  await expect(page).toHaveURL(/node=/);
  await expect(
    page.getByText("Trades exposed", { exact: true }).or(page.getByText(/None of the trades/)),
  ).toBeVisible();
  await accessible(page);
});

test("a fixing is added and deleted on the lifecycle screen", async ({ page }) => {
  await signInAs(page, "trader");
  await page.goto("/trading/lifecycle");
  // a Sunday: the demo's closes have no fixing there, so the test overwrites nothing and leaves the series as it was
  await page.getByRole("textbox", { name: /Add fixings of/ }).fill("2021-06-27 99.25");
  await page.getByRole("button", { name: "Add", exact: true }).click();
  await expect(page.getByText("Fixings added")).toBeVisible();
  const row = page.getByRole("row", { name: /27 Jun 2021/ });
  await expect(row).toContainText("99.25");
  await row.getByRole("button", { name: "delete" }).click();
  await expect(page.getByRole("row", { name: /27 Jun 2021/ })).toHaveCount(0);
  await accessible(page);
});

test("the pricing policy shows its starting values, and a trader saves a version", async ({ page }) => {
  await signInAs(page, "trader");
  await page.goto("/trading/costs");
  const cell = page.getByRole("textbox", { name: /^Overrepo, up to/ }).first();
  await expect(cell).toBeVisible();
  // a value other than the one saved, so there is something to save on every run
  await cell.fill((await cell.inputValue()) === "-6" ? "-7" : "-6");
  await cell.blur();
  await page.getByRole("button", { name: "Save a new version" }).click();
  await expect(page.getByText("Cost table saved")).toBeVisible();
  await accessible(page);
});

test("the explained P&L says what it needs, or explains the period", async ({ page }) => {
  await signInAs(page, "trader");
  await page.goto("/trading/pnl");
  await expect(page.getByText("No explained P&L yet").or(page.getByText("The Greeks predicted"))).toBeVisible();
  await accessible(page);
});
