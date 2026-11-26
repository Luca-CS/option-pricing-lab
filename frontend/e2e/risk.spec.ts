import { accessible, ensureUsers, expect, signInAs, test } from "./fixtures";

test.beforeAll(async ({ baseURL }) => {
  await ensureUsers(baseURL ?? "", ["risk"]);
});

test("the risk manager runs a short VaR and reads it", async ({ page }) => {
  test.setTimeout(600_000); // the job may wait behind another in the queue
  await signInAs(page, "risk");
  await page.goto("/risk/var");
  const run = page.getByRole("button", { name: "Run the VaR" });
  await expect(run).toBeVisible();
  // a short window, without the stressed year's search: a couple of minutes on a runner's CPU
  await page.getByRole("textbox", { name: "Window, business days" }).fill("20");
  await page.getByRole("checkbox", { name: "Stressed" }).uncheck();
  await page.getByRole("checkbox", { name: "10 days" }).uncheck();
  await run.click();
  await expect(page).toHaveURL(/job=/);
  await expect(page.getByRole("heading", { name: "The P&L over the window" })).toBeVisible({ timeout: 540_000 });
  await expect(page.getByRole("heading", { name: /^The VaR day: / })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Contributions" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "The worst moves" })).toBeVisible();
  await expect(page.getByText(/^20 historical moves/)).toBeVisible();
  await accessible(page);
  await page.getByRole("link", { name: "Backtest this VaR on the explained P&L" }).click();
  await expect(
    page.getByRole("heading", { name: "Each day against the VaR" }).or(page.getByText("Nothing to test yet")),
  ).toBeVisible();
  await accessible(page);
});

test("the risk manager backfills two closes and compares them", async ({ page }) => {
  test.setTimeout(600_000);
  await signInAs(page, "risk");
  await page.goto("/risk/runs");
  await page.getByLabel("From", { exact: true }).fill("2021-06-29");
  await page.getByLabel("To", { exact: true }).fill("2021-06-30");
  await page.getByRole("checkbox", { name: "With the Greeks" }).uncheck();
  await page.getByRole("button", { name: "Run the backfill" }).click();
  // a backfill asked again reuses its runs: the same two closes, computed or reused
  await expect(page.getByText(/^2 closes from 29 Jun 2021 to 30 Jun 2021/)).toBeVisible({ timeout: 540_000 });
  // a close may hold two runs (with and without the Greeks): either will do
  await page
    .getByRole("checkbox", { name: /the close of 2021-06-29$/ })
    .first()
    .check();
  await expect(page.getByRole("heading", { name: "Positions" })).toBeVisible();
  await page
    .getByRole("checkbox", { name: /the close of 2021-06-30$/ })
    .first()
    .check();
  await expect(page).toHaveURL(/a=eod-2021-06-29.*&b=eod-2021-06-30/);
  await expect(page.getByRole("link", { name: /^Explain the P&L from 29 Jun 2021 to 30 Jun 2021/ })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Trades" })).toBeVisible();
  await accessible(page);
});

test("the day's vol move opens by maturity, under each spot-vol rule", async ({ page }) => {
  test.setTimeout(900_000);
  await signInAs(page, "risk");
  // the day the backfill above explained: its vol move tenor by tenor, quick, under sticky strike
  await page.goto("/trading/pnl?day=2021-06-30");
  await expect(page.getByRole("heading", { name: "The vol move, opened" })).toBeVisible();
  const panel = page.getByRole("region", { name: "The vol move, opened" });
  await panel.getByText("Sticky strike", { exact: true }).click();
  await panel.getByText("Tenor by tenor", { exact: true }).click();
  await panel.getByText("Quick, 20,000 paths", { exact: true }).click();
  const open = panel.getByRole("button", { name: /^(Open the day|Open at this precision|Opened)$/ });
  if ((await open.textContent()) !== "Opened") await open.click();
  await expect(page.getByRole("table", { name: /under each spot-vol rule$/ })).toBeVisible({ timeout: 840_000 });
  await expect(page.getByRole("table", { name: /vol P&L of 2021-06-30 by maturity$/ })).toBeVisible();
  await expect(page.getByText(/^Sticky strike \(opened below\)$/)).toBeVisible();
  await expect(page.getByText(/revaluations of [\d,]+ paths; the nodes and the residual/)).toBeVisible();
  await accessible(page);
});

test("the risk manager signs a version of the library, then the one before again", async ({ page }) => {
  await signInAs(page, "risk");
  await page.goto("/risk/stress");
  const library = page.getByRole("table", { name: "The scenarios of the library" });
  await expect(library).toBeVisible();
  const before = await library.getByRole("row").count();
  const sign = async (note: string) => {
    await page.getByRole("button", { name: "Sign a new version" }).click();
    const dialog = page.getByRole("dialog", { name: "Sign a new version of the library" });
    await dialog.getByRole("textbox", { name: "Note" }).fill(note);
    await dialog.getByRole("button", { name: "Sign", exact: true }).click();
    await expect(page.getByText(/^Library version \d+ signed$/)).toBeVisible();
    await expect(dialog).toBeHidden();
  };
  // a copy of a scenario, signed, then removed and signed again: the library ends as it began, two versions later
  await page.getByRole("button", { name: "Edit the library" }).click();
  await page.getByRole("button", { name: "Copy Rates up" }).click();
  await expect(library.getByRole("row")).toHaveCount(before + 1);
  await page.getByRole("button", { name: "Edit Rates up (copy)" }).click();
  const drawer = page.getByRole("dialog", { name: "Edit Rates up (copy)" });
  await expect(drawer.getByText("Read as")).toBeVisible();
  await accessible(page);
  await drawer.getByRole("button", { name: "Keep it" }).click();
  await sign("end-to-end test: a copy");
  await expect(page.getByRole("cell", { name: "Rates up (copy)" })).toBeVisible();
  await page.getByRole("button", { name: "Edit the library" }).click();
  await page.getByRole("button", { name: "Remove Rates up (copy)" }).click();
  await sign("end-to-end test: the copy removed");
  await expect(library.getByRole("row")).toHaveCount(before);
  await expect(page.getByRole("heading", { name: "Versions" })).toBeVisible();
  await accessible(page);
});
