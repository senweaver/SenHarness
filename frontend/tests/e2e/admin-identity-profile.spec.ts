/**
 * Admin — view another identity's Memory profile (USER.md + SOUL.md).
 *
 * 1. Bootstrap a regular identity + write a marker line to their
 *    USER.md via the authenticated `me/profile` endpoint.
 * 2. Log in as platform_admin and open
 *    `/admin/identities/{id}/profile` — the seeded marker text should
 *    be visible.
 *
 * Skips when no platform_admin seed is available.
 */
import { expect, test } from "@playwright/test";
import {
    bootstrapIdentity,
    bootstrapPlatformAdmin,
    randomSuffix,
    requireStack,
    seedAdminSession,
} from "./helpers";

test.beforeEach(async ({ baseURL }) => {
    await requireStack(baseURL);
});

test("platform admin can view another identity's memory profile", async ({
    baseURL,
    request,
    page,
}) => {
    const admin = await bootstrapPlatformAdmin(request, baseURL!);
    const user = await bootstrapIdentity(request, baseURL!);

    // Invite user to the admin's workspace via the invitation flow
    // (there is no direct POST /members endpoint).
    const inviteRes = await request.post(
        `${baseURL}/api/v1/workspaces/${admin.workspaceId}/invitations`,
        {
            headers: { Authorization: `Bearer ${admin.accessToken}` },
            data: { email: user.email, role: "member" },
        },
    );
    // Accept 201 (new invite) or 409 (already a member / duplicate invite).
    expect(
        [201, 409],
        `invite status ${inviteRes.status()}`,
    ).toContain(inviteRes.status());

    if (inviteRes.status() === 201) {
        const inv = (await inviteRes.json()) as { code: string };
        const acceptRes = await request.post(
            `${baseURL}/api/v1/workspaces/invitations/accept`,
            {
                headers: { Authorization: `Bearer ${user.accessToken}` },
                data: { code: inv.code },
            },
        );
        // 200 = joined, 409 = already member — both are fine.
        expect(
            [200, 409],
            `accept invite status ${acceptRes.status()}`,
        ).toContain(acceptRes.status());
    }

    const marker = `e2e-profile-marker-${randomSuffix()}`;
    // Seed the profile in the admin's workspace context — the admin profile
    // page reads via /memory-profiles/identities/{id} which is workspace-scoped.
    const put = await request.put(
        `${baseURL}/api/v1/memory-profiles/me/profile`,
        {
            headers: {
                Authorization: `Bearer ${user.accessToken}`,
                "X-Workspace-Id": admin.workspaceId,
            },
            data: { content_md: `# About\n\n- marker: ${marker}` },
        },
    );
    expect(put.ok(), `seed profile status ${put.status()}: ${await put.text()}`).toBe(true);

    await seedAdminSession(page, admin);
    await page.goto(`/en-US/admin/identities/${user.identityId}/profile`, {
        waitUntil: "domcontentloaded",
    });
    await expect(page.getByTestId("admin-identity-profile-page")).toBeVisible({
        timeout: 20_000,
    });
    await expect(page.getByText(marker).first()).toBeVisible({
        timeout: 10_000,
    });
});
