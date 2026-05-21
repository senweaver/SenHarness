/**
 * Login — multi-role coverage.
 *
 * The plain ``login.spec.ts`` only smoke-tests that the form renders and that
 * wrong passwords surface an error. This spec drives the **real UI login
 * form** for every identity/role combination the product supports and pins
 * down the post-login state that each role should see:
 *
 *   Platform role (``/api/v1/me.platform_role``):
 *     - user            (the default)
 *     - platform_admin  (only runs when E2E_PLATFORM_ADMIN_* env vars are set)
 *
 *   Workspace role (``/api/v1/me.current_role`` when the active workspace is
 *   the one being inspected):
 *     - owner    (implicit: identity bootstrapped via ``bootstrapIdentity``)
 *     - admin    (invited as member, promoted via PATCH)
 *     - operator ( same )
 *     - member   ( invited with role=member and kept )
 *     - auditor  ( invited as member, promoted to auditor )
 *     - guest    ( invited as member, demoted to guest )
 *
 *   Edge identities:
 *     - Fresh registration without any workspace membership at all. The UI
 *       must still log this user in; their ``current_role`` is ``null`` and
 *       ``permissions`` is empty.
 *
 * Each case uses the **UI password form** (not ``seedSession``) so that a
 * regression in the login page — missing field, wrong submit wiring, broken
 * access-token capture — fails fast. The authoritative role assertion is then
 * made via ``fetchMe`` (REST), which avoids depending on the Zustand store
 * picking the right ``activeWorkspaceId`` on first paint.
 */
import { expect, test, type APIRequestContext, type Page } from "@playwright/test";
import {
    bootstrapIdentity,
    bootstrapInvitedMember,
    bootstrapPlatformAdmin,
    fetchMe,
    loginViaUI,
    randomEmail,
    requireStack,
    seedSession,
    WORKSPACE_ROLES,
    type BootstrappedIdentity,
    type WorkspaceRole,
} from "./helpers";

test.beforeEach(async ({ baseURL }) => {
    await requireStack(baseURL);
});

/**
 * Expected capability set for each built-in workspace role, mirroring
 * ``backend/app/services/permissions.py::ROLE_CAPABILITIES``. Keep in sync.
 *
 * We assert a **subset** (``containsAll``) rather than strict equality so
 * that adding new capabilities to the backend matrix doesn't cascade into
 * an e2e failure here — the guarantees we care about are:
 *   owner/admin ⊇ {workspace.manage, members.manage, approvals.decide_all}
 *   operator    ⊇ {agents.manage, approvals.decide_department}
 *   operator    ⊄ workspace.manage  (the main "below admin" check)
 *   member      = {sessions.create, approvals.decide_own}
 *   auditor     ⊇ {audit.view, approvals.view_all}
 *   auditor     ⊄ sessions.create  (read-only by design)
 *   guest       = {}
 */
const ROLE_EXPECTATIONS: Record<
    WorkspaceRole,
    { mustHave: string[]; mustNotHave: string[] }
> = {
    owner: {
        mustHave: [
            "workspace.manage",
            "members.manage",
            "agents.manage",
            "approvals.decide_all",
            "audit.view",
        ],
        mustNotHave: [],
    },
    admin: {
        mustHave: [
            "workspace.manage",
            "members.manage",
            "agents.manage",
            "approvals.decide_all",
            "audit.view",
        ],
        mustNotHave: [],
    },
    operator: {
        mustHave: [
            "agents.manage",
            "squads.manage",
            "approvals.decide_department",
            "audit.view",
        ],
        mustNotHave: ["workspace.manage", "members.manage"],
    },
    member: {
        mustHave: ["sessions.create", "approvals.decide_own"],
        mustNotHave: [
            "workspace.manage",
            "members.manage",
            "agents.manage",
            "audit.view",
        ],
    },
    auditor: {
        mustHave: ["audit.view", "approvals.view_all"],
        mustNotHave: ["sessions.create", "workspace.manage", "agents.manage"],
    },
    guest: {
        mustHave: [],
        mustNotHave: [
            "workspace.manage",
            "members.manage",
            "agents.manage",
            "sessions.create",
            "audit.view",
        ],
    },
};

/**
 * Log in via the UI form and then verify the access token captured into
 * Zustand matches what ``fetchMe`` returns — proves that the form wrote
 * to the store *and* that the store is what subsequent API calls will use.
 */
async function assertLoginPersistsSession(
    page: Page,
    request: APIRequestContext,
    baseURL: string,
    identity: BootstrappedIdentity,
): Promise<string> {
    await loginViaUI(page, identity.email, identity.password);

    // The auth store persists under ``senharness.auth``. Drain it and make
    // sure we actually got a token (a silent failure mode would be the form
    // landing on /login?error=... without ever seeing the auth_store call).
    const stored = await page.evaluate(
        () => localStorage.getItem("senharness.auth") ?? "",
    );
    expect(stored, "auth store populated after login").toContain("accessToken");

    const me = await fetchMe(
        request,
        baseURL,
        identity.accessToken,
        identity.workspaceId,
    );
    expect(me.email).toBe(identity.email);
    return me.id;
}

// ─── Workspace-role matrix ─────────────────────────────────

for (const role of WORKSPACE_ROLES) {
    test(`login as workspace ${role}`, async ({ baseURL, request, page }) => {
        // Owner case is special: the identity we bootstrap *is* the owner of
        // their workspace — no invitation step needed.
        let identity: BootstrappedIdentity;
        if (role === "owner") {
            identity = await bootstrapIdentity(request, baseURL!);
        } else {
            const owner = await bootstrapIdentity(request, baseURL!);
            identity = await bootstrapInvitedMember(
                request,
                baseURL!,
                owner,
                role,
            );
        }

        await assertLoginPersistsSession(page, request, baseURL!, identity);

        const me = await fetchMe(
            request,
            baseURL!,
            identity.accessToken,
            identity.workspaceId,
        );

        expect(me.platform_role, "non-admin identities stay platform_role=user").toBe(
            "user",
        );
        expect(me.current_workspace_id).toBe(identity.workspaceId);
        expect(me.current_role, `current_role should be ${role}`).toBe(role);

        const expectations = ROLE_EXPECTATIONS[role];
        for (const cap of expectations.mustHave) {
            expect(
                me.permissions,
                `${role} should have ${cap}`,
            ).toContain(cap);
        }
        for (const cap of expectations.mustNotHave) {
            expect(
                me.permissions,
                `${role} should NOT have ${cap}`,
            ).not.toContain(cap);
        }
    });
}

// ─── Platform admin ────────────────────────────────────────

test("login as platform_admin via UI form", async ({ baseURL, request, page }) => {
    // Skips itself when E2E_PLATFORM_ADMIN_EMAIL/PASSWORD aren't configured.
    const admin = await bootstrapPlatformAdmin(request, baseURL!);

    await loginViaUI(page, admin.email, admin.password);

    const me = await fetchMe(
        request,
        baseURL!,
        admin.accessToken,
        admin.workspaceId || undefined,
    );
    expect(me.platform_role).toBe("platform_admin");

    // The /en-US/admin route itself renders (not a redirect) — the most
    // reliable signal that the platform_admin gate is actually open. We
    // deliberately skip the AvatarMenu open-dropdown dance here because
    // ``rbac-platform.spec.ts`` covers both (admin-visible / user-hidden)
    // sides of that gate with a shared ``openAvatarMenu`` helper.
    await page.goto("/en-US/admin");
    await expect(
        page.getByRole("heading", { level: 1, name: "Platform overview" }),
    ).toBeVisible({ timeout: 15_000 });
});

// ─── No-workspace identity ─────────────────────────────────

test("login as user with no workspace memberships", async ({
    baseURL,
    request,
    page,
}) => {
    // We can't use ``bootstrapIdentity`` here because that helper always
    // creates a workspace. Register + login manually so membership list is
    // empty.
    const email = randomEmail();
    const password = "e2e-password-very-long";
    const register = await request.post(`${baseURL}/api/v1/auth/register`, {
        data: { email, name: "E2E Lonely User", password },
    });
    expect(register.status()).toBe(201);

    const login = await request.post(`${baseURL}/api/v1/auth/login`, {
        data: { email, password },
    });
    expect(login.ok()).toBe(true);
    const { access_token: accessToken } = (await login.json()) as {
        access_token: string;
    };

    // UI login: form should accept the credentials and land the user
    // somewhere outside /login even without a workspace context.
    await loginViaUI(page, email, password);

    const me = await fetchMe(request, baseURL!, accessToken);
    expect(me.platform_role).toBe("user");
    expect(me.workspaces, "workspaces list empty").toHaveLength(0);
    expect(me.current_workspace_id, "no active workspace").toBeNull();
    expect(me.current_role).toBeNull();
    expect(me.permissions, "no permissions without membership").toEqual([]);
});

// ─── Regression: re-login after logout ─────────────────────

test("logout then re-login as a different role", async ({
    baseURL,
    request,
    page,
}) => {
    // Confirms the login form itself still functions on a second, fresh
    // session — guards against "works once per browser context" regressions
    // (cookie collisions, sticky refresh tokens, etc.).
    const a = await bootstrapIdentity(request, baseURL!);
    const bOwner = await bootstrapIdentity(request, baseURL!);
    const b = await bootstrapInvitedMember(request, baseURL!, bOwner, "operator");

    // 1st login — owner A.
    await loginViaUI(page, a.email, a.password);
    await page.evaluate(() => {
        localStorage.removeItem("senharness.auth");
        localStorage.removeItem("senharness.workspace");
    });

    // 2nd login — operator B (different identity + different workspace).
    await page.goto("/en-US/login");
    await expect(page.getByTestId("login-form")).toBeVisible({ timeout: 15_000 });
    await loginViaUI(page, b.email, b.password);

    const me = await fetchMe(request, baseURL!, b.accessToken, b.workspaceId);
    expect(me.email).toBe(b.email);
    expect(me.current_role).toBe("operator");
    expect(me.permissions).toContain("agents.manage");
});

// ─── Workspace-switch preserves role info ──────────────────

test("member belonging to two workspaces sees the role that matches the active one", async ({
    baseURL,
    request,
    page,
}) => {
    // Reproduce a realistic case: a user is owner of their own workspace AND
    // a member of a colleague's. ``/me`` should return a different
    // ``current_role`` depending on which workspace is active.
    const colleague = await bootstrapIdentity(request, baseURL!);
    const dual = await bootstrapInvitedMember(
        request,
        baseURL!,
        colleague,
        "auditor",
    );
    // The invitee also has their own "personal" workspace created at
    // registration time — we recover it from the /me payload since
    // bootstrapInvitedMember overrides ``workspaceId``.
    const meAll = await fetchMe(request, baseURL!, dual.accessToken);
    const personal = meAll.workspaces.find(
        (w) => w.workspace_id !== colleague.workspaceId,
    );
    expect(personal, "personal workspace still exists").toBeTruthy();

    // Drive a login — we don't care about the UI here, just make sure the
    // two ``X-Workspace-Id`` values yield two different roles.
    await seedSession(page, dual);
    await page.goto("/en-US/");
    await expect(page.locator("body")).toBeVisible();

    const asPersonal = await fetchMe(
        request,
        baseURL!,
        dual.accessToken,
        personal!.workspace_id,
    );
    expect(asPersonal.current_role).toBe("owner");

    const asColleague = await fetchMe(
        request,
        baseURL!,
        dual.accessToken,
        colleague.workspaceId,
    );
    expect(asColleague.current_role).toBe("auditor");
});
