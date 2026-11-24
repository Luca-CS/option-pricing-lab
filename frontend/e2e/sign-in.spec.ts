import { accessible, expect, signIn, test, user } from "./fixtures";

test("the page is served under the strict CSP", async ({ page }) => {
  const response = await page.goto("/");
  const csp = response?.headers()["content-security-policy"] ?? "";
  expect(csp).toContain("script-src 'self'");
  expect(csp).toContain("style-src 'self'");
  expect(csp).not.toContain("unsafe-inline");
});

test("a deep link without a session goes through the sign-in and back", async ({ page }) => {
  await page.goto("/trading/trades/DEMO-01");
  await expect(page.getByRole("heading", { name: "Sign in" })).toBeVisible();
  await accessible(page);
  await signIn(page);
  await expect(page).toHaveURL(/\/trading\/trades\/DEMO-01$/);
  await expect(page.getByRole("heading", { name: `Signed in as ${user}` })).toBeVisible();
  await accessible(page);
});

test("a wrong password is refused with the server's words", async ({ page }) => {
  await page.goto("/");
  await signIn(page, user, "not the password at all");
  await expect(page.getByRole("alert")).toContainText(/name or password/i);
  // the right one resets the count of failures
  await signIn(page);
  await expect(page.getByRole("heading", { name: `Signed in as ${user}` })).toBeVisible();
});

test("signing out ends the session", async ({ page }) => {
  await page.goto("/");
  await signIn(page);
  await page.getByRole("button", { name: "Sign out" }).click();
  await expect(page.getByRole("heading", { name: "Sign in" })).toBeVisible();
  await page.reload();
  await expect(page.getByRole("heading", { name: "Sign in" })).toBeVisible();
});
