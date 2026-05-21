/**
 * Invitation flow spec.
 *
 * Scenario:
 *   1. Workspace A's owner issues an invitation via API (owner is
 *      bootstrapped through the standard `bootstrapIdentity()`).
 *   2. A second identity (Workspace B user) is bootstrapped and seeded
 *      into the browser.
 *   3. The second user opens `/en-US/invite/[code]` and clicks
 *      "接受邀请" (the accept button, tagged `invite-accept-submit`).
 *   4. After the accept round-trip we assert the invited user now shows
 *      up in Workspace A's member list (API call with A's token).
 *
 * We keep the invite-page locator hardcoded to the Chinese CTA because
 * the page intentionally hardcodes Chinese copy today — switching to a
 * testid decouples the spec from that copy while a future i18n pass is
 * planned.
 */
import { expect, test } from "@playwright/test";
import {
    authHeaders,
    bootstrapIdentity,
    requireStack,
    seedSession,
} from "./helpers";

test.beforeEach(async ({ baseURL }) => {
    await requireStack(baseURL);
});

test("invite code flow: create -> accept -> membership visible", async ({
    baseURL,
    request,
    page,
}) => {
    const owner = await bootstrapIdentity(request, baseURL!);
    const invitee = await bootstrapIdentity(request, baseURL!);

    const inviteRes = await request.post(
        `${baseURL}/api/v1/workspaces/${owner.workspaceId}/invitations`,
        {
            headers: authHeaders(owner),
            data: {
                email: invitee.email,
                role: "member",
                expires_in_hours: 72,
            },
        },
    );
    expect(inviteRes.status(), "create invitation").toBe(201);
    const invitation = (await inviteRes.json()) as { code: string };

    await seedSession(page, invitee);
    await page.goto(`/en-US/invite/${invitation.code}`);
    await expect(page.getByTestId("invite-accept-page")).toBeVisible({
        timeout: 15_000,
    });
    await page.getByTestId("invite-accept-submit").click();

    // On success the page redirects to `/` — but that's a home page whose
    // shell is heavy on auth/workspace context, so we'd rather assert the
    // authoritative API result: the invitee now belongs to the owner's
    // workspace.
    await page.waitForURL(/\/$|\/en-US\/$/, { timeout: 15_000 }).catch(() => {});

    const members = await request.get(
        `${baseURL}/api/v1/workspaces/${owner.workspaceId}/members`,
        { headers: authHeaders(owner) },
    );
    expect(members.ok()).toBe(true);
    const arr = (await members.json()) as Array<{ identity_email: string | null }>;
    expect(arr.map((m) => m.identity_email)).toContain(invitee.email);
});
