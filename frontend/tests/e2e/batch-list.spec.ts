/**
 * Batch — list page smoke.
 *
 * When there are zero runs the list renders a "No batch runs yet." empty
 * card; we assert the heading is present regardless so the spec catches
 * render regressions even on a cold workspace.
 */
import { expect, test } from "@playwright/test";
import { bootstrapIdentity, requireStack, seedSession } from "./helpers";

test.beforeEach(async ({ baseURL }) => {
    await requireStack(baseURL);
});

test("batch list page mounts", async ({ baseURL, request, page }) => {
    const identity = await bootstrapIdentity(request, baseURL!);
    await seedSession(page, identity);
    await page.goto("/en-US/batch");
    await expect(
        page.getByRole("heading", { level: 1, name: "Batch replay" }),
    ).toBeVisible({ timeout: 15_000 });
});
