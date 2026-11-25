// The public demo's end-to-end tests (e2e-demo): the front built in demo mode (`npm run build:demo`, dist-demo) under
// the Pages path, served by Vite's preview, with no desk behind it: every answer comes from the recorded sessions.
import { defineConfig, devices } from "@playwright/test";

const port = Number(process.env.DEMO_PORT ?? 4174);
const base = process.env.OPTLAB_DEMO_BASE ?? "/option-pricing-lab/";

export default defineConfig({
  testDir: "e2e-demo",
  workers: 1,
  fullyParallel: false,
  forbidOnly: Boolean(process.env.CI),
  retries: 0,
  reporter: process.env.CI ? [["list"], ["html", { open: "never", outputFolder: "playwright-report-demo" }]] : "list",
  use: { baseURL: `http://127.0.0.1:${port}${base}`, trace: "retain-on-failure", screenshot: "only-on-failure" },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"], viewport: { width: 1440, height: 900 } } }],
  webServer: {
    command: `npx vite preview --mode demo --outDir dist-demo --port ${port} --strictPort`,
    url: `http://127.0.0.1:${port}${base}`,
    timeout: 60_000,
    reuseExistingServer: !process.env.CI,
  },
});
