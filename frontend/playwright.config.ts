import { defineConfig, devices } from "@playwright/test";

/**
 * End-to-end configuration.
 *
 * The browser suite needs a running stack (API + worker + SPA) and the
 * deterministic fake ANAF service.  This project does not start the backend
 * itself: point it at a running stack with `E2E_BASE_URL` / `E2E_API_ORIGIN` (the
 * repository-root integration harness `make e2e` does exactly that).  Activation
 * links come from the API's development mailbox, so no file access is needed.
 */
export default defineConfig({
  testDir: "./e2e",
  timeout: 60_000,
  expect: { timeout: 10_000 },
  fullyParallel: false,
  workers: 1,
  reporter: [["list"]],
  use: {
    baseURL: process.env.E2E_BASE_URL ?? "http://127.0.0.1:5173",
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
  },
  projects: [
    {
      name: "desktop-chromium",
      use: { ...devices["Desktop Chrome"] },
    },
    {
      // critical flows must also work on a narrow mobile viewport
      name: "mobile-chromium",
      use: { ...devices["Pixel 7"] },
    },
  ],
});
