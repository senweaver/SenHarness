import { defineConfig, devices } from "@playwright/test";

// Assumes the stack is up on localhost:3000 with a real backend.
// Specs `test.skip` themselves when the API isn't reachable.
export default defineConfig({
    testDir: "./tests/e2e",
    fullyParallel: true,
    forbidOnly: !!process.env.CI,
    retries: process.env.CI ? 2 : 1,
    workers: process.env.CI ? 2 : undefined,
    reporter: process.env.CI ? "github" : "list",
    timeout: 60_000,
    expect: { timeout: 10_000 },
    use: {
        baseURL: process.env.PLAYWRIGHT_BASE_URL ?? "http://localhost:3000",
        trace: "retain-on-failure",
        screenshot: "only-on-failure",
        video: "retain-on-failure",
    },
    projects: [
        {
            name: "chromium",
            use: { ...devices["Desktop Chrome"] },
        },
    ],
    webServer: process.env.PLAYWRIGHT_SKIP_WEBSERVER
        ? undefined
        : {
              command: "pnpm start",
              url: "http://localhost:3000",
              reuseExistingServer: !process.env.CI,
              timeout: 120_000,
          },
});
