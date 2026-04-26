/**
 * E2E configuration — headed mode with full screenshots and HTML report.
 *
 * Run with:
 *   pnpm exec playwright test --config playwright.e2e.config.ts
 *
 * Requires the full stack to be running:
 *   - Backend at http://localhost:8000
 *   - Frontend (pnpm dev or pnpm start) at http://localhost:3000
 */
import { defineConfig, devices } from "@playwright/test";
import path from "path";

const REPORT_DIR = path.resolve(__dirname, "../assets/playwright-report");

export default defineConfig({
    testDir: "./e2e",
    fullyParallel: false,
    forbidOnly: false,
    retries: 1,
    workers: 2,
    timeout: 90_000,
    expect: { timeout: 15_000 },

    outputDir: path.resolve(__dirname, "../assets/test-results"),

    reporter: [
        ["list"],
        ["html", { outputFolder: REPORT_DIR, open: "never" }],
        ["json", { outputFile: path.join(REPORT_DIR, "results.json") }],
    ],

    use: {
        baseURL: process.env.PLAYWRIGHT_BASE_URL ?? "http://localhost:3000",
        headless: false,
        screenshot: "on",
        video: "retain-on-failure",
        trace: "retain-on-failure",
        viewport: { width: 1280, height: 800 },
        launchOptions: {
            slowMo: 100,
        },
    },

    projects: [
        {
            name: "chromium-headed",
            use: {
                ...devices["Desktop Chrome"],
                headless: false,
                viewport: { width: 1280, height: 800 },
                launchOptions: { slowMo: 100 },
            },
        },
    ],

    webServer: process.env.PLAYWRIGHT_SKIP_WEBSERVER
        ? undefined
        : {
              command: "pnpm dev",
              url: "http://localhost:3000",
              reuseExistingServer: true,
              timeout: 120_000,
          },
});
