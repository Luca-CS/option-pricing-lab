import { SPACES } from "../src/app/spaces";
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

test("every screen passes axe, in both themes", async ({ page }) => {
  test.setTimeout(600_000);
  await page.goto("/");
  await signIn(page);
  // every screen of every space, and the pages of an entity of each kind the demo folder holds
  const screens = [
    ...SPACES.flatMap((s) => s.screens.map((x) => x.path)),
    "/trading/trades/DEMO-01",
    "/trading/lifecycle/DEMO-01",
    "/quant/market/SPX",
  ];
  for (const scheme of ["dark", "light"]) {
    await page.evaluate((s) => localStorage.setItem("optlab-color-scheme", s), scheme);
    for (const screen of screens) {
      await page.goto(screen);
      await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
      await page.waitForLoadState("networkidle");
      await test.step(`${screen} (${scheme})`, () => accessible(page));
    }
  }
});

test("the keyboard's focus is always visible", async ({ page }) => {
  await signInAs(page, "trader");
  await page.goto("/trading/book");
  await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
  for (let i = 0; i < 25; i++) {
    await page.keyboard.press("Tab");
    const ring = await page.evaluate(() => {
      const el = document.activeElement as HTMLElement | null;
      if (!el || el === document.body) return "body";
      // a segmented control's radio is hidden: its label carries the ring
      const box = el.getBoundingClientRect();
      const shownOn = box.width <= 1 && el.id ? (document.querySelector(`label[for="${el.id}"]`) ?? el) : el;
      const s = getComputedStyle(shownOn);
      const shown = (s.outlineStyle !== "none" && Number.parseFloat(s.outlineWidth) > 0) || s.boxShadow !== "none";
      const name = el.getAttribute("aria-label") ?? el.textContent?.trim().slice(0, 40) ?? "";
      return shown ? "ok" : `${el.tagName.toLowerCase()} ${name}`;
    });
    expect(ring === "ok" || ring === "body", `no visible focus on: ${ring}`).toBe(true);
  }
});

test("the theme changes from the keyboard and the menu, within the page's CSP", async ({ page }) => {
  // Mantine drops the transitions with a <style> it writes on each change, which the CSP refuses (a console error,
  // which fails the test): the desk keeps its transitions instead
  await signInAs(page, "trader");
  const html = page.locator("html");
  const before = await html.getAttribute("data-mantine-color-scheme");
  await page.keyboard.press("t");
  await expect(html).not.toHaveAttribute("data-mantine-color-scheme", before ?? "dark");
  await page.getByRole("button", { name: /preferences and sign out/ }).click();
  await page.getByText("Graphite", { exact: true }).click();
  await expect(html).toHaveAttribute("data-mantine-color-scheme", "dark");
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
