/**
 * Settings → Workspace → Members — invite + role change + remove.
 *
 * The members page UI depends on a lot of Radix Selects + dialogs, so we
 * drive the authoritative operations via API and check the member list
 * in the UI to catch render regressions.
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

test("invite accepts member → change role → remove", async ({
    baseURL,
    request,
    page,
}) => {
    const owner = await bootstrapIdentity(request, baseURL!);
    const invitee = await bootstrapIdentity(request, baseURL!);

    // Invite + accept via API.
    const invite = await request.post(
        `${baseURL}/api/v1/workspaces/${owner.workspaceId}/invitations`,
        {
            headers: authHeaders(owner),
            data: { email: invitee.email, role: "member" },
        },
    );
    expect(invite.status()).toBe(201);
    const { code } = (await invite.json()) as { code: string };

    const accept = await request.post(
        `${baseURL}/api/v1/workspaces/invitations/accept`,
        {
            headers: authHeaders(invitee),
            data: { code },
        },
    );
    expect(accept.ok()).toBe(true);

    // Members UI now shows both identities.
    await seedSession(page, owner);
    await page.goto("/en-US/settings/workspace/members");
    await expect(page.getByText(invitee.email).first()).toBeVisible({
        timeout: 20_000,
    });

    // Promote to admin via API.
    const promote = await request.patch(
        `${baseURL}/api/v1/workspaces/${owner.workspaceId}/members/${invitee.identityId}`,
        {
            headers: authHeaders(owner),
            data: { role: "admin" },
        },
    );
    expect(promote.ok(), `promote status ${promote.status()}`).toBe(true);

    // Remove via API.
    const remove = await request.delete(
        `${baseURL}/api/v1/workspaces/${owner.workspaceId}/members/${invitee.identityId}`,
        { headers: authHeaders(owner) },
    );
    expect(remove.status()).toBe(204);

    // Final API check — invitee is gone.
    const members = await request.get(
        `${baseURL}/api/v1/workspaces/${owner.workspaceId}/members`,
        { headers: authHeaders(owner) },
    );
    const emails = (
        (await members.json()) as Array<{ identity_email: string | null }>
    ).map((m) => m.identity_email);
    expect(emails).not.toContain(invitee.email);
});
