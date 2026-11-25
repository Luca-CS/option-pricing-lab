// The bridges between the spaces (docs/ux.md §3), each followed as a click on a real link. The vega node down to its
// trades and its surface (bridge 4) is followed in trading.spec.ts, the VaR to its backtest in risk.spec.ts.
import { accessible, ensureUsers, expect, type Page, signInAs, test } from "./fixtures";

test.beforeAll(async ({ baseURL }) => {
  await ensureUsers(baseURL ?? "", ["structurer+trader", "quant"]);
});

/** A light quote of the Phoenix preset; its id. */
async function aQuote(page: Page): Promise<string> {
  await page.goto("/structuring/builder?preset=phoenix");
  await page.getByText("Quick", { exact: true }).click();
  const go = page.getByRole("button", { name: "Quote", exact: true });
  await expect(go).toBeEnabled();
  await go.click();
  await expect(page.getByText(/Coupon solved for a note price of/)).toBeVisible({ timeout: 240_000 });
  const id = await page
    .getByText(/ · quote [\w-]+$/)
    .first()
    .textContent();
  return (id ?? "").split(" · quote ").at(-1)?.trim() ?? "";
}

test("a quote opens its booking, its model risk and the pricing policy", async ({ page }) => {
  test.setTimeout(300_000); // the quote may wait behind another job in the queue
  await signInAs(page, "structurer+trader");
  const quote = await aQuote(page);
  expect(quote).not.toBe("");
  // bridge 9: the desk's lines → the pricing policy in force
  await page.getByRole("link", { name: "The pricing policy" }).click();
  await expect(page).toHaveURL(/\/trading\/costs$/);
  // bridge 1: a quote's link anywhere, "Book it" → its page with the booking dialog open
  await page.goto("/structuring/quotes");
  await page
    .getByRole("button", { name: `Open ${quote} in…` })
    .first()
    .click();
  await page.getByRole("menuitem", { name: "Book it" }).click();
  await expect(page).toHaveURL(new RegExp(`/structuring/quotes/${quote}\\?book=1$`));
  const dialog = page.getByRole("dialog", { name: "Book this quote" });
  await expect(dialog).toBeVisible();
  await accessible(page);
  await dialog.getByRole("button", { name: "Cancel" }).click(); // nothing booked: the book stays as it was
  // bridge 2: the quote → its model risk
  await page.getByRole("link", { name: "Model risk", exact: true }).click();
  await expect(page).toHaveURL(new RegExp(`/quant/calibration\\?quote=${quote}$`));
  await expect(page.getByRole("heading", { name: `The model risk of quote ${quote}` })).toBeVisible();
  await expect(page.getByText(/Local vol|Black-Scholes|Heston|SLV/).first()).toBeVisible();
  await accessible(page);
  // the quote's sheet copies as JSON (to the clipboard, or shown selected where the page may not write it)
  await page.goto(`/structuring/quotes/${quote}`);
  await page.getByRole("button", { name: "Copy term sheet" }).click();
  const shown = page.getByRole("dialog", { name: "Term sheet (JSON)" });
  await expect(page.getByText("Term sheet copied as JSON").or(shown)).toBeVisible();
  if (await shown.isVisible()) await page.keyboard.press("Escape");
  // bridge 2: the quote → its backtest, the quote's own product struck on a past date
  await page.getByRole("link", { name: "Backtest" }).click();
  await expect(page).toHaveURL(new RegExp(`/quant/replay\\?quote=${quote}$`));
  await expect(page.getByText(/The backtest of quote/)).toContainText("phoenix preset on SPX");
  await accessible(page);
});

test("a trade opens its P&L explained and its replay; a name lists its trades", async ({ page }) => {
  await signInAs(page, "structurer+trader");
  // bridge 3: a trade → its P&L explained, its replay
  await page.goto("/trading/trades/DEMO-01");
  await page.getByRole("link", { name: "Its P&L explained" }).click();
  await expect(page).toHaveURL(/\/trading\/pnl\?trade=DEMO-01$/);
  await expect(
    page
      .getByRole("heading", { name: "DEMO-01 over the period, step by step" })
      .or(page.getByText("No explained P&L yet")),
  ).toBeVisible();
  await page.goto("/trading/trades/DEMO-01");
  await page.getByRole("link", { name: "Its replay on past closes" }).click();
  await expect(page).toHaveURL(/\/quant\/replay\?trade=DEMO-01$/);
  // bridge 5: a name's market → the trades exposed to it
  await page.goto("/quant/market/SPX");
  const on = page.getByRole("table", { name: "The trades and quotes on SPX" });
  await expect(on).toBeVisible();
  await on.getByRole("link", { name: "DEMO-01" }).click();
  await expect(page).toHaveURL(/\/trading\/trades\/DEMO-01$/);
  await accessible(page);
});

test("an origin badge opens what the lake holds", async ({ page }) => {
  await signInAs(page, "quant");
  // bridge 10: an origin → the lake's coverage, scrolled to
  await page.goto("/quant/market");
  await page
    .getByRole("link", { name: /^surface: / })
    .first()
    .click();
  await expect(page).toHaveURL(/\/quant\/market#lake$/);
  await expect(page.getByRole("table", { name: "The lake's datasets" })).toBeInViewport();
  await expect(page.getByRole("table", { name: "The lake's datasets" })).toContainText("synthetic");
  // the baskets against their members (v0's Data screen): pseudo-quotes, synthetic by construction
  await expect(page.getByRole("table", { name: "CvC and PvP pseudo-quotes" })).toContainText("SPX + NDX");
  await accessible(page);
});
