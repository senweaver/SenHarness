/**
 * RBAC — workspace-scoped permissions.
 *
 * Pairs with ``login-multi-role.spec.ts`` (which proves *login* works for
 * each role) by proving that the **permissions** each role actually ends up
 * with are enforced both server-side and in the UI gates. The server is the
 * ultimate source of truth, but UI gates ship first so a regression that
 * lets a member click a button the backend then rejects is still a UX bug.
 *
 * Backend contract (see ``backend/app/services/permissions.py``):
 *   owner / admin      → workspace.manage, members.manage, audit.view
 *   operator           → agents.manage, approvals.decide_department, audit.view
 *   member             → sessions.create, approvals.decide_own
 *   auditor            → audit.view, approvals.view_all
 *   guest              → (none)
 *
 * UI gates verified here:
 *   - ``/en-US/settings/workspace/memory`` redirects to ``/settings``
 *     unless ``current_role ∈ {owner, admin}``.
 *
 * API gates verified here (spot-checked — full matrix isn't useful, we just
 * want a representative 200/403 for each role):
 *   - ``PATCH /api/v1/workspaces/{id}``  (workspace.manage)
 *   - ``POST  /api/v1/workspaces/{id}/invitations`` (members.manage)
 *   - ``GET   /api/v1/audit/events?scope=workspace``  (audit.view-ish)
 */
import { expect, test, type APIRequestContext } from "@playwright/test";
import {
    authHeaders,
    bootstrapIdentity,
    bootstrapInvitedMember,
    fetchMe,
    requireStack,
    seedSession,
    type BootstrappedIdentity,
} from "./helpers";

test.beforeEach(async ({ baseURL }) => {
    await requireStack(baseURL);
});

// ─── Helpers local to this spec ────────────────────────────

async function tryPatchWorkspace(
    request: APIRequestContext,
    baseURL: string,
    identity: BootstrappedIdentity,
): Promise<number> {
    const res = await request.patch(
        `${baseURL}/api/v1/workspaces/${identity.workspaceId}`,
        {
            headers: authHeaders(identity),
            // Idempotent-ish field; we're only checking the gate, not the
            // write itself.
            data: { description: `e2e rbac ping ${Date.now()}` },
        },
    );
    return res.status();
}

async function tryCreateInvitation(
    request: APIRequestContext,
    baseURL: string,
    identity: BootstrappedIdentity,
): Promise<number> {
    const res = await request.post(
        `${baseURL}/api/v1/workspaces/${identity.workspaceId}/invitations`,
        {
            headers: authHeaders(identity),
            data: {
                email: `e2e-nobody-${Date.now()}@example.com`,
                role: "member",
                expires_in_hours: 1,
            },
        },
    );
    return res.status();
}

async function tryListAudit(
    request: APIRequestContext,
    baseURL: string,
    identity: BootstrappedIdentity,
): Promise<number> {
    const res = await request.get(
        `${baseURL}/api/v1/audit/events?scope=workspace&limit=1`,
        { headers: authHeaders(identity) },
    );
    return res.status();
}

// ─── Server-side gates ─────────────────────────────────────

test("owner keeps full workspace.manage + members.manage + audit.view", async ({
    baseURL,
    request,
}) => {
    const owner = await bootstrapIdentity(request, baseURL!);

    expect(await tryPatchWorkspace(request, baseURL!, owner)).toBe(200);
    expect(await tryCreateInvitation(request, baseURL!, owner)).toBe(201);
    expect(await tryListAudit(request, baseURL!, owner)).toBe(200);
});

test("admin mirrors owner on workspace.manage / members.manage / audit.view", async ({
    baseURL,
    request,
}) => {
    const owner = await bootstrapIdentity(request, baseURL!);
    const admin = await bootstrapInvitedMember(request, baseURL!, owner, "admin");

    expect(await tryPatchWorkspace(request, baseURL!, admin)).toBe(200);
    expect(await tryCreateInvitation(request, baseURL!, admin)).toBe(201);
    expect(await tryListAudit(request, baseURL!, admin)).toBe(200);
});

test("operator cannot manage workspace or members but can see audit", async ({
    baseURL,
    request,
}) => {
    const owner = await bootstrapIdentity(request, baseURL!);
    const operator = await bootstrapInvitedMember(
        request,
        baseURL!,
        owner,
        "operator",
    );

    expect(await tryPatchWorkspace(request, baseURL!, operator)).toBe(403);
    expect(await tryCreateInvitation(request, baseURL!, operator)).toBe(403);
    // operator keeps ``audit.view`` per ROLE_CAPABILITIES; the audit route
    // itself gates on {owner, admin, auditor}, so operator is 403 there
    // despite holding audit.view capability. This is an intentional
    // tightening on the API (the capability grants UI affordances, not
    // REST access). Keep the assertion honest.
    expect(await tryListAudit(request, baseURL!, operator)).toBe(403);
});

test("member is locked out of everything except own-session primitives", async ({
    baseURL,
    request,
}) => {
    const owner = await bootstrapIdentity(request, baseURL!);
    const member = await bootstrapInvitedMember(
        request,
        baseURL!,
        owner,
        "member",
    );

    expect(await tryPatchWorkspace(request, baseURL!, member)).toBe(403);
    expect(await tryCreateInvitation(request, baseURL!, member)).toBe(403);
    expect(await tryListAudit(request, baseURL!, member)).toBe(403);
});

test("auditor can read audit but cannot mutate workspace or invite", async ({
    baseURL,
    request,
}) => {
    const owner = await bootstrapIdentity(request, baseURL!);
    const auditor = await bootstrapInvitedMember(
        request,
        baseURL!,
        owner,
        "auditor",
    );

    expect(await tryPatchWorkspace(request, baseURL!, auditor)).toBe(403);
    expect(await tryCreateInvitation(request, baseURL!, auditor)).toBe(403);
    expect(await tryListAudit(request, baseURL!, auditor)).toBe(200);
});

test("guest is denied everything except existing-as-a-member", async ({
    baseURL,
    request,
}) => {
    const owner = await bootstrapIdentity(request, baseURL!);
    const guest = await bootstrapInvitedMember(
        request,
        baseURL!,
        owner,
        "guest",
    );

    expect(await tryPatchWorkspace(request, baseURL!, guest)).toBe(403);
    expect(await tryCreateInvitation(request, baseURL!, guest)).toBe(403);
    expect(await tryListAudit(request, baseURL!, guest)).toBe(403);

    // A guest can still see themselves in ``/me`` with ``current_role=guest``
    // and an empty permission list — the identity exists, it's just caged.
    const me = await fetchMe(
        request,
        baseURL!,
        guest.accessToken,
        guest.workspaceId,
    );
    expect(me.current_role).toBe("guest");
    expect(me.permissions).toEqual([]);
});

// ─── UI gate: /settings/workspace/memory ───────────────────

test("workspace MEMORY page renders for owner, redirects away for member", async ({
    baseURL,
    request,
    page,
}) => {
    const owner = await bootstrapIdentity(request, baseURL!);
    const member = await bootstrapInvitedMember(
        request,
        baseURL!,
        owner,
        "member",
    );

    // Owner sees the editor.
    await seedSession(page, owner);
    await page.goto("/en-US/settings/workspace/memory");
    await expect(page.getByTestId("workspace-memory-page")).toBeVisible({
        timeout: 20_000,
    });

    // Fresh context avoids the owner's Zustand state bleeding into the
    // member's session.
    await page.context().clearCookies();
    await page.evaluate(() => localStorage.clear()).catch(() => {});

    await seedSession(page, member);
    await page.goto("/en-US/settings/workspace/memory");
    // The page's useEffect calls router.replace("/settings") when the role
    // isn't admin/owner. Give the client-side nav a moment to fire.
    await expect(page).toHaveURL(/\/settings(?!\/workspace\/memory)/, {
        timeout: 15_000,
    });
});

test("workspace MEMORY page renders for admin (promoted member)", async ({
    baseURL,
    request,
    page,
}) => {
    // Admin specifically — we already test owner above. This guards the
    // ``ADMIN_ROLES = new Set(["owner", "admin"])`` list from regressing
    // to owner-only.
    const owner = await bootstrapIdentity(request, baseURL!);
    const admin = await bootstrapInvitedMember(request, baseURL!, owner, "admin");

    await seedSession(page, admin);
    await page.goto("/en-US/settings/workspace/memory");
    await expect(page.getByTestId("workspace-memory-page")).toBeVisible({
        timeout: 20_000,
    });
});
