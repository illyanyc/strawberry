import { defineConfig, devices } from "@playwright/test";

/** Browser end-to-end tests against a running stack (`make up`, then `make e2e-ui`). */
export default defineConfig({
  testDir: "./e2e",
  timeout: 120_000,
  fullyParallel: false,
  workers: 1,
  reporter: [["list"]],
  use: {
    baseURL: process.env.TB_E2E_BASE_URL ?? "http://localhost:8080",
    trace: "retain-on-failure",
    viewport: { width: 1280, height: 800 },
  },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"], viewport: { width: 1280, height: 800 } } }],
});
