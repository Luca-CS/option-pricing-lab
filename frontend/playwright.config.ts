// End-to-end tests against the desk itself: the API serves the built front (OPTLAB_FRONTEND) on a demo data folder,
// under the production CSP. E2E_DATA is a folder made by `optlab demo data --to DIR --password-stdin` and
// E2E_PASSWORD its account's password (`demo`, admin). E2E_BASE_URL points at a desk already running instead.
import path from "node:path";
import { defineConfig, devices } from "@playwright/test";

const port = Number(process.env.E2E_PORT ?? 8771);
const external = process.env.E2E_BASE_URL;
const baseURL = external ?? `http://127.0.0.1:${port}`;

export default defineConfig({
  testDir: "e2e",
  testIgnore: ["record/**"], // the demo's recorder runs on its own (playwright.record.config.ts)
  // logins are counted per address and per account (docs/architecture/api.md §2): one browser at a time
  workers: 1,
  fullyParallel: false,
  forbidOnly: Boolean(process.env.CI),
  retries: 0,
  reporter: process.env.CI ? [["list"], ["html", { open: "never" }]] : "list",
  use: { baseURL, trace: "retain-on-failure", screenshot: "only-on-failure" },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"], viewport: { width: 1440, height: 900 } } }],
  webServer: external
    ? undefined
    : {
        command: `uv run --no-sync optlab --data "${process.env.E2E_DATA ?? ""}" serve --port ${port} --engine numpy`,
        url: `${baseURL}/api/v1/ready`,
        timeout: 180_000,
        reuseExistingServer: !process.env.CI,
        env: { OPTLAB_FRONTEND: path.resolve(import.meta.dirname, "dist"), OPTLAB_LOG: "WARNING" },
      },
});
