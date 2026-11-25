import { accessible, ensureUsers, expect, signIn, signInAs, test } from "./fixtures";

test.beforeAll(async ({ baseURL }) => {
  await ensureUsers(baseURL ?? "", ["trader", "structurer", "quant"]);
});

test("each role lands on its home and sees its own space first; Admin is an admin's", async ({ page }) => {
  await signInAs(page, "trader");
  await expect(page).toHaveURL(/\/trading$/);
  const rail = page.getByRole("navigation", { name: "Spaces" });
  await expect(rail.getByRole("link", { name: "Trading" })).toBeVisible();
  await expect(rail.getByRole("link", { name: "Quant" })).toBeVisible();
  await expect(rail.getByRole("link", { name: "Admin" })).toHaveCount(0);
  await page.goto("/admin");
  await expect(page.getByText("Nothing at this address")).toBeVisible();

  await page.context().clearCookies();
  await signInAs(page, "structurer");
  await expect(page).toHaveURL(/\/structuring$/);
  await expect(page.getByRole("link", { name: "Phoenix" })).toBeVisible();
});

test("the palette finds a trade and opens its page", async ({ page }) => {
  await page.goto("/");
  await signIn(page);
  await expect(page).toHaveURL(/\/admin$/);
  await page.keyboard.press("Control+K");
  await page.getByPlaceholder(/A trade, a quote/).fill("DEMO-03");
  await page.keyboard.press("Enter");
  await expect(page).toHaveURL(/\/trading\/trades\/DEMO-03$/);
  await expect(page.getByRole("heading", { name: "Trade DEMO-03" })).toBeVisible();
});

test("g then a letter goes to a space, ? lists the keys", async ({ page }) => {
  await signInAs(page, "quant");
  await expect(page).toHaveURL(/\/quant$/);
  await page.keyboard.press("g");
  await page.keyboard.press("t");
  await expect(page).toHaveURL(/\/trading$/);
  await page.keyboard.press("Shift+?");
  await expect(page.getByRole("dialog", { name: "Keyboard" })).toBeVisible();
});

test("a job runs in the background, shows in the tray and says when it is done", async ({ page }) => {
  await signInAs(page, "trader");
  await page.getByRole("button", { name: "Revalue the book" }).click();
  const done = page.getByText("Book valuation is done");
  await expect(done).toBeVisible({ timeout: 120_000 });
  await page.getByRole("link", { name: "Open the result" }).click();
  await expect(page).toHaveURL(/\/jobs\/[0-9a-f]+$/);
  await expect(page.getByText("done", { exact: true })).toBeVisible();
  await expect(page.getByRole("link", { name: "Open the book" })).toBeVisible();
  await page.getByRole("button", { name: "Jobs" }).click();
  await expect(page.getByRole("link", { name: "Book valuation" })).toBeVisible();
});

test("the market drawer says what market the numbers are on", async ({ page }) => {
  await signInAs(page, "trader");
  await page.getByRole("banner").getByRole("button", { name: /SPX/ }).click();
  const drawer = page.getByRole("dialog", { name: "Market in use" });
  await expect(drawer).toBeVisible();
  await expect(drawer.getByText(/^SPX-\d{8}T\d{4}-/).first()).toBeVisible();
  await expect(drawer.getByText("surface: reconstructed")).toBeVisible();
});

test("every home passes axe, in both themes", async ({ page }) => {
  await page.goto("/");
  await signIn(page);
  for (const scheme of ["dark", "light"]) {
    await page.evaluate((s) => localStorage.setItem("optlab-color-scheme", s), scheme);
    for (const home of ["/structuring", "/trading", "/risk", "/quant", "/admin"]) {
      await page.goto(home);
      await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
      await page.waitForLoadState("networkidle");
      await accessible(page);
    }
  }
});

test("the shell holds at phone width without scrolling sideways", async ({ page }) => {
  await page.setViewportSize({ width: 375, height: 812 });
  await signInAs(page, "trader");
  await expect(page.getByRole("heading", { name: "Trading" })).toBeVisible();
  const overflow = await page.evaluate(
    () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
  );
  expect(overflow).toBeLessThanOrEqual(0);
});
