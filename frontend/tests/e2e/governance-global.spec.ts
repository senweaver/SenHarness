/**
 * Governance — platform_admin creates a GLOBAL-scope policy.
 *
 * GLOBAL-scope policies span every tenant and only platform admins can
 * create them. We seed one via API (UI path would require opening the
 * scope Select, which is Radix-portaled and brittle) and confirm the
 * `/admin/governance` page renders the row.
 *
 * Skips when no platform_admin seed env vars are configured.
 */
import { expect, test } from "@playwright/test";
import {
    bootstrapPlatformAdmin,
    randomSuffix,
    requireStack,
    seedAdminSession,
} from "./helpers";

test.beforeEach(async ({ baseURL }) => {
    await requireStack(baseURL);
});

test("platform admin creates a GLOBAL policy", async ({
    baseURL,
    request,
    page,
}) => {
    const admin = await bootstrapPlatformAdmin(request, baseURL!);
    const name = `E2E Global Policy ${randomSuffix()}`;

    const create = await request.post(`${baseURL}/api/v1/governance/policies`, {
        headers: {
            Authorization: `Bearer ${admin.accessToken}`,
            "X-Workspace-Id": admin.workspaceId,
        },
        data: {
            name,
            description: "e2e global-scope policy",
            scope: "global",
            enabled: true,
            priority: 50,
            rules_json: {
                blocklist: { keywords: ["e2e-blocked"] },
            },
            metadata_json: {},
        },
    });
    expect(create.status(), `create status ${create.status()}`).toBe(201);

    await seedAdminSession(page, admin);
    await page.goto("/en-US/admin/governance");
    await expect(page.getByTestId("governance-page")).toBeVisible({
        timeout: 15_000,
    });
    await expect(page.getByText(name).first()).toBeVisible({
        timeout: 10_000,
    });
});
