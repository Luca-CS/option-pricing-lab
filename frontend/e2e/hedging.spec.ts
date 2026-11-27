import { accessible, ensureUsers, expect, signInAs, submitAs, test } from "./fixtures";

test.beforeAll(async ({ baseURL }) => {
  await ensureUsers(baseURL ?? "", ["trader"]);
});

test("the trader reads the hedges, promotes a what-if and books a future from the ticket", async ({ page }) => {
  test.setTimeout(240_000);
  await signInAs(page, "trader");
  await page.goto("/trading/hedging");
  await expect(page.getByRole("heading", { name: "The hedges" })).toBeVisible();
  await expect(page.getByText(/ES \w{3}\d{2} future/).first()).toBeVisible();
  await expect(page.getByText(/^IRS USD 5y fixed/)).toBeVisible();
  const whatIf = page.getByRole("row", { name: /put \(OTC\)/ });
  await expect(whatIf.getByText("what-if")).toBeVisible();
  await accessible(page);
  await whatIf.getByRole("button", { name: "Promote" }).click();
  await expect(page.getByText(/: promote done$/)).toBeVisible();
  await expect(page.getByRole("row", { name: /put \(OTC\)/ }).getByText("live", { exact: true })).toBeVisible();

  await page.getByRole("tab", { name: "Ticket" }).click();
  await page.getByRole("combobox", { name: "Instrument" }).click();
  await page.getByRole("option", { name: "Index future" }).click();
  await page.getByRole("combobox", { name: "Underlying" }).click();
  await page.getByRole("option", { name: "SPX", exact: true }).click();
  await page.getByLabel("Expiry").fill("2021-12-17");
  await page.getByRole("textbox", { name: /^Price/ }).fill("1000");
  await page.getByRole("button", { name: "Mark", exact: true }).click();
  await expect(page.getByText("Value per unit")).toBeVisible();
  await page.getByRole("button", { name: "Use the fair level" }).click();
  await page.getByRole("textbox", { name: "Size" }).fill("2");
  await page.getByRole("button", { name: "Book as a what-if" }).click();
  await expect(page.getByText(/^Booked as H-/)).toBeVisible();
  await accessible(page);
});

test("the optimizer proposes futures for the book's delta and books them as what-ifs", async ({ page, baseURL }) => {
  test.setTimeout(600_000);
  // a light run started through the API (delta only, futures only, few paths), followed by the screen from its link
  const job = await submitAs(baseURL ?? "", "trader", "/api/v1/trading/hedges/optimizations", {
    options: { precision: "quick", n_paths: 2_000 },
    families: ["delta"],
    universe: { vanillas: false, rates: false, dividends: false, funding: false },
    var_days: 0,
  });
  await signInAs(page, "trader");
  await page.goto(`/trading/hedging?view=optimizer&hedgeJob=${job}`);
  await expect(page.getByRole("heading", { name: "Targets", exact: true })).toBeVisible({ timeout: 480_000 });
  await expect(page.getByText(/^delta\|SPX\|/)).toBeVisible();
  await expect(page.getByRole("heading", { name: "Spot × vol, after" })).toBeVisible();
  await accessible(page);
  const book = page.getByRole("button", { name: "Book as what-ifs" });
  if (await book.isVisible()) {
    await book.click();
    await expect(page.getByText(/hedges booked$/)).toBeVisible();
  }
});
