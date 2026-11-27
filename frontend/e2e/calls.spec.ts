import { accessible, ensureUsers, expect, signInAs, test } from "./fixtures";

test.beforeAll(async ({ baseURL }) => {
  await ensureUsers(baseURL ?? "", ["trader"]);
});

test("the trader reads the calls to come, reviews them and calls a note", async ({ page }) => {
  test.setTimeout(300_000);
  await signInAs(page, "trader");
  await page.goto("/trading/calls");
  await expect(page.getByRole("heading", { name: "The calls to come" })).toBeVisible();
  const row = page.getByRole("row", { name: /DEMO-07/ });
  await expect(row).toBeVisible();
  await accessible(page);
  await page.getByRole("button", { name: "Review the calls" }).click();
  await expect(page).toHaveURL(/callJob=/);
  const review = page.getByRole("row", { name: /DEMO-07/ }).last();
  await expect(review.getByText(/^(call|keep|close)$/)).toBeVisible({ timeout: 240_000 });
  await accessible(page);
  await review.getByRole("button", { name: "Call" }).click();
  await page.getByRole("dialog").getByRole("button", { name: "Call" }).click();
  await expect(page.getByText(/DEMO-07 called on/)).toBeVisible();
});
