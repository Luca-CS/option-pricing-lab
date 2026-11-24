// Shared by every end-to-end test: the demo account, and a page that fails the test on any console error or CSP
// violation (a refused inline style shows up as one).
import AxeBuilder from "@axe-core/playwright";
import { test as base, expect, type Page, request } from "@playwright/test";

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

/** A password for a test account of a role, derived from the demo account's (never written anywhere). */
export function passwordOf(role: string): string {
  return `${password}-${role}-e2e`;
}

/** Accounts holding one role each (`e2e-trader`, ...), made by the demo admin through the API; already there is fine. */
export async function ensureUsers(baseURL: string, roles: string[]): Promise<void> {
  const ctx = await request.newContext({ baseURL });
  const login = await ctx.post("/api/v1/auth/login", { data: { name: user, password } });
  expect(login.status()).toBe(200);
  const { csrf_token } = (await login.json()) as { csrf_token: string };
  for (const role of roles) {
    const r = await ctx.post("/api/v1/admin/users", {
      data: { name: `e2e-${role}`, password: passwordOf(role), roles: [role] },
      headers: { "X-CSRF-Token": csrf_token },
    });
    expect([201, 200, 409]).toContain(r.status());
  }
  await ctx.dispose();
}

/** Signed in as the test account of a role, landed on its home. */
export async function signInAs(page: Page, role: string): Promise<void> {
  await page.goto("/sign-in");
  await signIn(page, `e2e-${role}`, passwordOf(role));
  await expect(page).not.toHaveURL(/sign-in/);
}

/** No serious or critical accessibility violation on the page as it is (axe-core). */
export async function accessible(page: Page): Promise<void> {
  const results = await new AxeBuilder({ page }).analyze();
  const bad = results.violations.filter((v) => v.impact === "serious" || v.impact === "critical");
  expect(bad.map((v) => `${v.id}: ${v.help} (${v.nodes.length})`)).toEqual([]);
}
