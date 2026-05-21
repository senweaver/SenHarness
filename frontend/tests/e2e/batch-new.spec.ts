/**
 * Batch — new run creation via UI.
 *
 * The form at `/en-US/batch/new` has no data-testids so we lean on
 * `getByLabel` for the two required text inputs and the Radix Select
 * combobox for the Candidate Agent. After submit we assert the detail
 * page landed (`h1="Batch run detail"`) and the API list contains the
 * newly-created run.
 */
import { expect, test } from "@playwright/test";
import {
    apiCreateAgent,
    authHeaders,
    bootstrapIdentity,
    randomSuffix,
    requireStack,
    seedSession,
} from "./helpers";

test.beforeEach(async ({ baseURL }) => {
    await requireStack(baseURL);
});

test("create a batch run via UI", async ({ baseURL, request, page }) => {
    const identity = await bootstrapIdentity(request, baseURL!);
    const agent = await apiCreateAgent(request, baseURL!, identity);

    await seedSession(page, identity);
    // Use domcontentloaded to avoid timing out on slow first-compile in dev
    // mode where asset loading can take > 30 s.
    await page.goto("/en-US/batch/new", { waitUntil: "domcontentloaded" });

    await expect(
        page.getByRole("heading", { level: 1, name: "New batch run" }),
    ).toBeVisible({ timeout: 30_000 });

    const runName = `E2E Batch ${randomSuffix()}`;
    await page.getByLabel("Name").fill(runName);
    await page
        .getByLabel("Description (optional)")
        .fill("e2e batch run");
    // Candidate agent Select — click to open, wait for option, then pick by name.
    await page.getByRole("combobox").click();
    await page.getByRole("option", { name: agent.name }).waitFor({ state: "visible", timeout: 10_000 });
    await page.getByRole("option", { name: agent.name }).click();

    // Fill the single default case textarea.
    const caseTextareas = page.locator(
        "textarea[placeholder='User prompt to replay…']",
    );
    await caseTextareas.first().fill("Respond with OK.");

    const submitBtn = page.getByRole("button", { name: "Create and start" });
    await expect(submitBtn).not.toBeDisabled({ timeout: 5_000 });
    await submitBtn.click();

    // Redirect to `/batch/{id}`.
    await expect(page).toHaveURL(/\/batch\/[0-9a-f-]{36}/, { timeout: 30_000 });
    // The detail page renders `<h1>{run.name}</h1>` via PageHeader — wait for
    // the async data fetch to complete before asserting.
    await expect(
        page.getByRole("heading", { level: 1, name: runName }),
    ).toBeVisible({ timeout: 20_000 });

    // Cross-check via API.
    const list = await request.get(`${baseURL}/api/v1/batch/runs`, {
        headers: authHeaders(identity),
    });
    expect(list.ok()).toBe(true);
    const names = ((await list.json()) as Array<{ name: string }>).map(
        (r) => r.name,
    );
    expect(names).toContain(runName);
});
