/**
 * Admin — cross-workspace approvals list + decide smoke.
 *
 * We can't easily seed a pending approval without a real tool-call
 * pipeline. Instead we smoke the admin list endpoint + page mount and
 * confirm `/api/v1/admin/approvals/{id}/decision` returns 404 for a
 * made-up id (wire check — a regression in the gate or routing would
 * surface as 500 / 403 instead).
 */
import { expect, test } from "@playwright/test";
import {
    bootstrapPlatformAdmin,
    requireStack,
    seedAdminSession,
} from "./helpers";

test.beforeEach(async ({ baseURL }) => {
    await requireStack(baseURL);
});

test("admin approvals page mounts + decide endpoint wired", async ({
    baseURL,
    request,
    page,
}) => {
    const admin = await bootstrapPlatformAdmin(request, baseURL!);

    await seedAdminSession(page, admin);
    await page.goto("/en-US/admin/approvals");
    await expect(
        page.getByRole("heading", {
            level: 1,
            name: "Cross-workspace approvals",
        }),
    ).toBeVisible({ timeout: 15_000 });

    // Decide on a non-existent approval — should return 404 (routing live,
    // auth gate passed, service returned "not found"). Anything else
    // (5xx / 403) means the route is broken for platform_admin.
    const fakeId = "00000000-0000-4000-8000-000000000000";
    const res = await request.post(
        `${baseURL}/api/v1/admin/approvals/${fakeId}/decision`,
        {
            headers: { Authorization: `Bearer ${admin.accessToken}` },
            data: { action: "approve", reason: "e2e" },
        },
    );
    expect(res.status(), `admin decide status ${res.status()}`).toBe(404);
});
