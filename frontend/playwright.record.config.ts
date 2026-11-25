// The recorder of the demo's sessions (e2e/record): like the end-to-end tests, a desk serving the built front on a
// fresh demo data folder (E2E_DATA, from `optlab demo data --to DIR --password-stdin`, and its E2E_PASSWORD), but on
// the fastest engine at hand (the runs the stories show are full ones) and with hours to spare.
import path from "node:path";
import { defineConfig, devices } from "@playwright/test";

const port = Number(process.env.E2E_PORT ?? 8774);
const baseURL = `http://127.0.0.1:${port}`;

export default defineConfig({
  testDir: "e2e/record",
  testMatch: "record.spec.ts",
  workers: 1,
  retries: 0,
  reporter: "list",
  use: { baseURL, trace: "retain-on-failure" },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"], viewport: { width: 1440, height: 900 } } }],
  webServer: {
    command: `uv run --no-sync optlab --data "${process.env.E2E_DATA ?? ""}" serve --port ${port} --engine ${process.env.E2E_ENGINE ?? "auto"}`,
    url: `${baseURL}/api/v1/ready`,
    timeout: 300_000,
    reuseExistingServer: false,
    env: { OPTLAB_FRONTEND: path.resolve(import.meta.dirname, "dist"), OPTLAB_LOG: "WARNING" },
  },
});
