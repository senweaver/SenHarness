/**
 * Settings — redirect shims.
 *
 * `/settings` itself is a deliberate client-side redirect to
 * `/settings/workspace/branding`. Regressions here usually surface as
 * a bookmark 404 — cheap smoke to catch that.
 */
import { expect, test } from "@playwright/test";
import { bootstrapIdentity, requireStack, seedSession } from "./helpers";

test.beforeEach(async ({ baseURL }) => {
    await requireStack(baseURL);
});

test("/settings redirects to /settings/workspace/branding", async ({
    baseURL,
    request,
    page,
}) => {
    const identity = await bootstrapIdentity(request, baseURL!);
    await seedSession(page, identity);
    await page.goto("/en-US/settings");
    await expect(page).toHaveURL(/\/settings\/workspace\/branding$/, {
        timeout: 15_000,
    });
});
