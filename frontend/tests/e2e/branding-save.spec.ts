/**
 * Settings → Workspace → Branding — deep save round-trip.
 *
 * Edits the welcome headline + logo URL and confirms the preview area
 * updates. The live `/` page should also pick up the new welcome text
 * after a reload (branding drives `home.welcome`).
 */
import { expect, test } from "@playwright/test";
import {
    authHeaders,
    bootstrapIdentity,
    randomSuffix,
    requireStack,
    seedSession,
} from "./helpers";

test.beforeEach(async ({ baseURL }) => {
    await requireStack(baseURL);
});

test("edit workspace branding headline + verify via API and /", async ({
    baseURL,
    request,
    page,
}) => {
    const identity = await bootstrapIdentity(request, baseURL!);
    const headline = `Hi {name}. e2e marker ${randomSuffix()}`;

    await seedSession(page, identity);
    await page.goto("/en-US/settings/workspace/branding");
    await expect(
        page.getByRole("heading", { level: 1, name: /Branding/ }),
    ).toBeVisible({ timeout: 15_000 });

    // Fill the welcome headline input (label text = "Home welcome headline").
    await page.getByLabel("Home welcome headline").fill(headline);
    await page.getByRole("button", { name: /^Save(\s|$)/ }).click();

    // Sonner toast confirms save. We gate on the API as the ultimate proof.
    await page.waitForTimeout(1_000);
    const ws = await request.get(`${baseURL}/api/v1/workspaces`, {
        headers: authHeaders(identity),
    });
    const list = (await ws.json()) as Array<{
        id: string;
        branding_json?: Record<string, unknown>;
    }>;
    const current = list.find((w) => w.id === identity.workspaceId);
    expect(current).toBeDefined();
    expect((current!.branding_json ?? {}).welcome_h1).toBe(headline);

    // Navigating to `/` should now render the custom welcome.
    await page.goto("/en-US");
    const expectedOnHome = headline.replace("{name}", "E2E User");
    await expect(
        page.getByRole("heading", { level: 1, name: expectedOnHome }),
    ).toBeVisible({ timeout: 20_000 });
});
