/**
 * Admin-only operations — workspace patch + KEK rotate.
 *
 * Uses `bootstrapPlatformAdmin()` which gracefully skips when no admin
 * seed is available. Both operations run through the REST API to keep
 * the spec deterministic; UI is exercised only to the extent of
 * asserting the admin can land on each page.
 */
import { expect, test } from "@playwright/test";
import {
    authHeaders,
    bootstrapIdentity,
    bootstrapPlatformAdmin,
    requireStack,
    seedAdminSession,
} from "./helpers";

test.beforeEach(async ({ baseURL }) => {
    await requireStack(baseURL);
});

test("admin can patch a workspace's plan", async ({ baseURL, request, page }) => {
    const admin = await bootstrapPlatformAdmin(request, baseURL!);
    await seedAdminSession(page, admin);

    // Bootstrap a brand-new workspace via a fresh identity so the patch
    // doesn't collide with whatever the admin already belongs to.
    const owner = await bootstrapIdentity(request, baseURL!);

    const patch = await request.patch(
        `${baseURL}/api/v1/admin/workspaces/${owner.workspaceId}`,
        {
            headers: { Authorization: `Bearer ${admin.accessToken}` },
            data: { plan: "team" },
        },
    );
    expect(patch.ok(), `patch status ${patch.status()}`).toBe(true);
    expect(((await patch.json()) as { plan: string }).plan).toBe("team");

    // Admin UI should land on the workspaces page without 5xx.
    await page.goto("/en-US/admin/workspaces");
    await expect(
        page.getByRole("heading", { level: 1, name: "Workspaces" }),
    ).toBeVisible({ timeout: 15_000 });
});

test("keyring rotate endpoint returns a new KEK", async ({ baseURL, request }) => {
    const admin = await bootstrapPlatformAdmin(request, baseURL!);

    const status = await request.get(`${baseURL}/api/v1/keyring/status`, {
        headers: authHeaders({ ...admin, workspaceId: admin.workspaceId }),
    });
    expect(status.ok(), `status ${status.status()}`).toBe(true);
    const before = (await status.json()) as { current_kek_id: string };

    const rotate = await request.post(`${baseURL}/api/v1/keyring/rotate`, {
        headers: { Authorization: `Bearer ${admin.accessToken}` },
        data: {},
    });
    if (!rotate.ok()) {
        const text = await rotate.text();
        if (/not.*configured|master.*key|missing|manual.*provider|rotate_manual/i.test(text)) {
            test.skip(
                true,
                `keyring rotation not available with this provider; got: ${text.slice(0, 160)}`,
            );
            return;
        }
        expect(rotate.ok(), `rotate status ${rotate.status()}: ${text}`).toBe(true);
    }
    const after = await request.get(`${baseURL}/api/v1/keyring/status`, {
        headers: { Authorization: `Bearer ${admin.accessToken}` },
    });
    const afterBody = (await after.json()) as { current_kek_id: string };
    expect(afterBody.current_kek_id).not.toBe(before.current_kek_id);
});
