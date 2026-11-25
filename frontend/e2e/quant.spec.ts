import { accessible, ensureUsers, expect, signInAs, test } from "./fixtures";

test.beforeAll(async ({ baseURL }) => {
  await ensureUsers(baseURL ?? "", ["quant", "quant+structurer"]);
});

test("the quant reads the market in use, down to a node of the surface", async ({ page }) => {
  await signInAs(page, "quant");
  await page.goto("/quant/market/SPX?strike=80%25&tenor=1y");
  await expect(page.getByRole("heading", { name: "SPX's market" })).toBeVisible();
  await expect(page.getByText(/^80% at 1y: \d+\.\d+ %$/)).toBeVisible();
  await expect(page.getByRole("heading", { name: "At the money" })).toBeVisible();
  await expect(page.getByRole("table", { name: "The lake's datasets" })).toBeVisible();
  await accessible(page);
});

test("a script is checked as it is typed, its error placed, then priced", async ({ page }) => {
  test.setTimeout(300_000); // the job may wait behind another in the queue
  await signInAs(page, "quant+structurer");
  await page.goto("/quant/script");
  const editor = page.getByRole("textbox", { name: "Payoff script" });
  await expect(page.getByText("checked", { exact: true })).toBeVisible();
  const good = await editor.inputValue();
  await editor.fill("underlying SPX\nparam N = 100\nevent 2022-06-30\n  pay N as\nend\n");
  await expect(page.getByRole("alert")).toHaveText(/^Line 4, column \d+: /);
  await editor.fill(good);
  await expect(page.getByText("checked", { exact: true })).toBeVisible();
  await page.getByRole("textbox", { name: "Paths" }).fill("5000");
  await page.getByRole("button", { name: "Price", exact: true }).click();
  await expect(page.getByText("per 100 of notional")).toBeVisible({ timeout: 240_000 });
  await accessible(page);
});

test("the quant replays a preset on the lake's closes and reads its life", async ({ page }) => {
  test.setTimeout(600_000);
  await signInAs(page, "quant");
  await page.goto("/quant/replay");
  await page.getByLabel("Strike date").fill("2020-01-02");
  // a light replay: few paths, a valuation every 20 business days
  await page.getByRole("textbox", { name: "Every, business days" }).fill("20");
  await page.getByRole("textbox", { name: "Paths" }).fill("2000");
  await page.getByRole("button", { name: "Replay", exact: true }).click();
  await expect(page.getByRole("heading", { name: "The P&L explained" })).toBeVisible({ timeout: 540_000 });
  await expect(page.getByRole("table", { name: "The product's events" })).toBeVisible();
  await expect(page.getByText("Struck", { exact: true })).toBeVisible();
  await accessible(page);
  await page
    .getByRole("link", { name: /^replay-.*-SPX-2020-01-02-lv$/ })
    .first()
    .click();
  await expect(page).toHaveURL(/\/quant\/replay\/replay-/);
  await expect(page.getByRole("heading", { name: "Value and cash" })).toBeVisible();
});

test("the vol lab, the calibration and the engine say what they hold", async ({ page }) => {
  await signInAs(page, "quant");
  await page.goto("/quant/vollab");
  await expect(
    page.getByText("No option quotes in this lake").or(page.getByRole("button", { name: "Fit the models" })),
  ).toBeVisible();
  await accessible(page);
  await page.goto("/quant/calibration");
  await expect(
    page
      .getByText("No model-risk table for this market")
      .or(page.getByRole("heading", { name: /exotics under each model/ })),
  ).toBeVisible();
  await accessible(page);
  await page.goto("/quant/engine");
  await expect(page.getByText("In use", { exact: true })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Devices" })).toBeVisible();
  await accessible(page);
});
