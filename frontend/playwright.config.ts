import { defineConfig, devices } from "@playwright/test";

// Browser end-to-end tests against the e2e Compose stack (`make e2e` starts it on fresh,
// seeded onramp_e2e and marketplace_e2e databases). The console under test is a production
// build served on its own port, so a running dev console is neither reused nor disturbed.
const PORT = 3100;
const API_URL = process.env.ONRAMP_E2E_API_URL ?? "http://127.0.0.1:8100";

export default defineConfig({
  testDir: "e2e",
  // The tests share one seeded world and the workflow worker acts on it; run them in order.
  workers: 1,
  fullyParallel: false,
  forbidOnly: !!process.env.CI,
  retries: 0,
  timeout: 90_000,
  expect: { timeout: 10_000 },
  reporter: [["list"]],
  use: {
    // localhost rather than 127.0.0.1: the production build sets the session cookie Secure,
    // and browsers accept Secure cookies over plain HTTP only for localhost.
    baseURL: `http://localhost:${PORT}`,
    trace: "retain-on-failure",
  },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"] } }],
  webServer: {
    command: `npm run build && npx next start --port ${PORT}`,
    url: `http://localhost:${PORT}/login`,
    timeout: 240_000,
    reuseExistingServer: false,
    env: { ONRAMP_API_URL: API_URL },
  },
});
