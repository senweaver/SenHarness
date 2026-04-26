import { defineConfig, devices } from "@playwright/test";

/**
 * SenHarness V1 E2E configuration.
 *
 * These tests assume a full stack is up on localhost:3000 (frontend)
 * with its API pointed at a real SenHarness backend. Locally:
 *
 *     docker compose up -d
 *     pnpm build && pnpm start      # or pnpm dev
 *     pnpm test:e2e
 *
 * In CI the whole compose stack is spun up by the GitHub Actions job
 * and these tests run against it.
 *
 * Tests under `e2e/*.spec.ts` that reach real endpoints call
 * `test.skip` when the stack isn't reachable so `pnpm test:e2e` on a
 * fresh checkout produces a green "skipped" report rather than a
 * confusing ECONNREFUSED wall.
 */
export default defineConfig({
    testDir: "./e2e",
    fullyParallel: true,
    forbidOnly: !!process.env.CI,
    retries: process.env.CI ? 2 : 1,
    // The V2 suite has 30+ specs; one worker keeps the backend contention
    // predictable on CI. Locally the default (logical cores) is fine.
    workers: process.env.CI ? 2 : undefined,
    reporter: process.env.CI ? "github" : "list",
    // Global hard cap — most specs settle well under this, but first-chat
    // and batch runs pull in the full agent kernel so give them headroom.
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
    // Start the Next.js server only if it's not already running. Avoids
    // a double-start race when the operator runs `pnpm dev` separately.
    webServer: process.env.PLAYWRIGHT_SKIP_WEBSERVER
        ? undefined
        : {
              command: "pnpm start",
              url: "http://localhost:3000",
              reuseExistingServer: !process.env.CI,
              timeout: 120_000,
          },
});
