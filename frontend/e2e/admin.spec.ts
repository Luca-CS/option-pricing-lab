import { accessible, expect, type Page, signIn, test } from "./fixtures";

// the demo's own account is the admin; the account this test changes is its own, so it leaves the others as they were
const TEMP = "e2e-temp";

async function signInAsAdmin(page: Page): Promise<void> {
  await page.goto("/sign-in");
  await signIn(page);
  await expect(page).not.toHaveURL(/sign-in/);
}

test("the admin adds an account, changes its roles, disables then enables it", async ({ page }) => {
  await signInAsAdmin(page);
  await page.goto("/admin/users");
  const table = page.getByRole("table", { name: "The desk's accounts" });
  await expect(table).toBeVisible();
  if ((await table.getByRole("row", { name: new RegExp(`^${TEMP}`) }).count()) === 0) {
    await page.getByRole("button", { name: "Add a user" }).click();
    const add = page.getByRole("dialog", { name: "Add a user" });
    await add.getByRole("textbox", { name: "Name" }).fill(TEMP);
    await add.getByRole("button", { name: "Generate" }).click();
    await accessible(page);
    await add.getByRole("button", { name: "Add", exact: true }).click();
    await expect(add).toBeHidden();
  }
  const row = table.getByRole("row", { name: new RegExp(`^${TEMP}`) });
  await expect(row).toBeVisible();
  const change = async () => {
    await page.getByRole("button", { name: `Change ${TEMP}` }).click();
    return page.getByRole("dialog", { name: `Change ${TEMP}` });
  };
  let dialog = await change();
  await dialog.getByRole("button", { name: "Disable" }).click();
  await expect(row).toContainText("disabled");
  dialog = await change();
  await dialog.getByRole("button", { name: "Enable" }).click();
  await expect(row).toContainText("active");
  await accessible(page);
});

test("the admin reads the jobs, the schedules, the audit log and the system", async ({ page }) => {
  await signInAsAdmin(page);
  await page.goto("/admin/jobs");
  await expect(page.getByRole("table", { name: "Every job of the desk" })).toBeVisible();
  await accessible(page);
  await page.goto("/admin/schedules");
  await expect(page.getByRole("heading", { name: "backup", exact: true })).toBeVisible();
  await expect(page.getByRole("group", { name: "Days of backup" })).toBeVisible();
  await accessible(page);
  await page.goto("/admin/audit");
  const log = page.getByRole("table", { name: "The audit log" });
  await expect(log).toBeVisible();
  await page.getByRole("textbox", { name: /Filter this page/ }).fill("login");
  await expect(log.getByRole("row").nth(1)).toContainText("login");
  await accessible(page);
  await page.goto("/admin/system");
  await expect(page.getByRole("heading", { name: "Workers" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Sharing" })).toBeVisible();
  await accessible(page);
});
