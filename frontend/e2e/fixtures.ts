// Shared by every end-to-end test: the demo account, and a page that fails the test on any console error or CSP
// violation (a refused inline style shows up as one).
import AxeBuilder from "@axe-core/playwright";
import { test as base, expect, type Page } from "@playwright/test";

export const user = process.env.E2E_USER ?? "demo";
export const password = process.env.E2E_PASSWORD ?? "";

export const test = base.extend<{ page: Page }>({
  page: async ({ page }, use) => {
    const problems: string[] = [];
    page.on("console", (m) => {
      if (m.type() === "error") problems.push(m.text());
    });
    page.on("pageerror", (e) => problems.push(e.message));
    await use(page);
    // a 401 is how the app learns it has no session: the browser logs it, it is not a fault
    expect(problems.filter((p) => !p.includes("401"))).toEqual([]);
  },
});

export { expect };

export async function signIn(page: Page, name = user, secret = password): Promise<void> {
  await page.getByRole("textbox", { name: "Name", exact: true }).fill(name);
  await page.getByRole("textbox", { name: "Password", exact: true }).fill(secret);
  await page.getByRole("button", { name: "Sign in" }).click();
}

/** No serious or critical accessibility violation on the page as it is (axe-core). */
export async function accessible(page: Page): Promise<void> {
  const results = await new AxeBuilder({ page }).analyze();
  const bad = results.violations.filter((v) => v.impact === "serious" || v.impact === "critical");
  expect(bad.map((v) => `${v.id}: ${v.help} (${v.nodes.length})`)).toEqual([]);
}
