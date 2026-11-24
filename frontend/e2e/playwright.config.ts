import { defineConfig } from "@playwright/test";

// Run through `npm run e2e` (e2e/run.mjs), which boots the servers and sets the URLs.
export default defineConfig({
  testDir: ".",
  testMatch: "**/*.e2e.ts",
  // One shared backend and database; the flows run one after another.
  workers: 1,
  timeout: 240_000,
  expect: { timeout: 30_000 },
  retries: process.env.CI ? 1 : 0,
  reporter: process.env.CI ? [["list"], ["html", { open: "never" }]] : "list",
  use: {
    baseURL: process.env.E2E_BASE_URL,
    // Tall enough that the whole board is inside the viewport, where mouse coordinates work.
    viewport: { width: 1280, height: 1100 },
    actionTimeout: 30_000,
    navigationTimeout: 30_000,
    trace: "retain-on-failure",
  },
  projects: [{ name: "chromium", use: { browserName: "chromium" } }],
});
