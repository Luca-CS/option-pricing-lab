import { accessible, ensureUsers, expect, signInAs, test } from "./fixtures";

test.beforeAll(async ({ baseURL }) => {
  await ensureUsers(baseURL ?? "", ["structurer+trader", "structurer"]);
});

async function quote(page: import("@playwright/test").Page, barrier: string) {
  await page.goto("/structuring/builder?preset=phoenix");
  const knockIn = page.getByRole("textbox", { name: "Knock-in barrier" });
  await expect(knockIn).toHaveValue("60");
  await knockIn.fill(barrier);
  await knockIn.press("Enter");
  await page.getByText("Quick", { exact: true }).click();
  const go = page.getByRole("button", { name: "Quote", exact: true });
  await expect(go).toBeEnabled();
  await go.click();
  await expect(page.getByText(/Coupon solved for a note price of/)).toBeVisible({ timeout: 180_000 });
}

test("the structurer builds a Phoenix, solves its coupon, and books it into the trader's book", async ({ page }) => {
  test.setTimeout(240_000); // the quote may wait behind another job in the queue
  await signInAs(page, "structurer+trader");
  await quote(page, "55");
  await expect(page.getByText(/% p\.a\./).first()).toBeVisible();
  await expect(page.getByRole("heading", { name: "The desk's lines" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "When it ends" })).toBeVisible();
  await accessible(page);
  await page.getByRole("button", { name: "Book it" }).click();
  const dialog = page.getByRole("dialog", { name: "Book this quote" });
  await dialog.getByRole("textbox", { name: "Counterparty" }).fill("E2E bank");
  await dialog.getByRole("button", { name: "Book", exact: true }).click();
  await expect(page).toHaveURL(/\/trading\/trades\/[\w-]+$/);
  await expect(page.getByText(/^Booked as /)).toBeVisible();
});

test("a field out of its bounds is refused under the field, and the quote waits", async ({ page }) => {
  await signInAs(page, "structurer");
  await page.goto("/structuring/builder?preset=phoenix");
  const barrier = page.getByRole("textbox", { name: "Coupon barrier" });
  await barrier.fill("500");
  await barrier.press("Enter");
  await expect(page.getByText(/above the maximum/)).toBeVisible();
  await expect(page.getByRole("button", { name: "Quote", exact: true })).toBeDisabled();
  await barrier.fill("70");
  await barrier.press("Enter");
  await expect(page.getByRole("button", { name: "Quote", exact: true })).toBeEnabled();
  // a structurer may quote but not book
  await expect(page.getByRole("button", { name: "Book it" })).toHaveCount(0);
});

test("the builder keeps its edits and its options in this browser, until started again or forgotten", async ({
  page,
}) => {
  await signInAs(page, "structurer");
  await page.goto("/structuring/builder?preset=phoenix");
  const knockIn = page.getByRole("textbox", { name: "Knock-in barrier" });
  await expect(knockIn).toHaveValue("60");
  await knockIn.fill("55");
  await knockIn.press("Enter");
  await page.getByRole("button", { name: "Pricing options" }).click();
  await page.getByRole("combobox", { name: "Delta convention" }).click();
  await page.getByRole("option", { name: "Sticky strike" }).click();
  await expect(page.getByRole("switch", { name: "Forward control variates" })).toBeVisible();
  await accessible(page);
  // away and back: the edit and the option are where they were
  await page.goto("/structuring/quotes");
  await page.goto("/structuring/builder?preset=phoenix");
  await expect(knockIn).toHaveValue("55");
  await page.getByRole("button", { name: "Pricing options" }).click();
  await expect(page.getByRole("combobox", { name: "Delta convention" })).toHaveValue("Sticky strike");
  await page.getByRole("button", { name: "Start again from the preset" }).click();
  await expect(knockIn).toHaveValue("60");
  // the user menu forgets the options too
  await page.getByRole("button", { name: /preferences and sign out/ }).click();
  await page.getByRole("menuitem", { name: "Forget this browser's preferences" }).click();
  await page.reload();
  await page.getByRole("button", { name: "Pricing options" }).click();
  await expect(page.getByRole("combobox", { name: "Delta convention" })).toHaveValue("Sticky moneyness");
});

test("two kept quotes compare side by side, with what differs in their sheets", async ({ page }) => {
  test.setTimeout(420_000); // two quotes, each of which may wait behind another job
  await signInAs(page, "structurer");
  await quote(page, "50");
  await page.goto("/structuring/quotes");
  const boxes = page.getByRole("checkbox", { name: /to compare/ });
  await expect(boxes.nth(1)).toBeVisible();
  await boxes.nth(0).check();
  await boxes.nth(1).check();
  await page.getByRole("link", { name: /Compare 2/ }).click();
  await expect(page).toHaveURL(/\/structuring\/compare\?quotes=/);
  await expect(page.getByRole("heading", { name: "Where their sheets differ" })).toBeVisible();
  await expect(page.getByText("downside.barrier")).toBeVisible();
  await accessible(page);
});
