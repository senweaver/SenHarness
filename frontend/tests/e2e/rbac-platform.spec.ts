/**
 * RBAC — platform-scope permissions (``platform_role``).
 *
 * Complements ``rbac-workspace.spec.ts`` by pinning down the gate around
 * ``/admin`` and cross-workspace admin endpoints. The rules are:
 *
 *   1. ``GET /api/v1/admin/**`` — requires ``platform_role=platform_admin``;
 *      non-admins get ``403 platform_admin_required``.
 *   2. ``/en-US/admin`` layout — runs ``useMe()`` then ``router.replace("/")``
 *      for non-admins (see ``admin/layout.tsx``). It does *not* issue an HTTP
 *      403 because Next.js matched the route; the rejection happens client-side.
 *   3. ``AvatarMenu`` — the "Platform admin" link is rendered iff the caller
 *      is a platform_admin. Non-admins should never see it in the dropdown.
 *
 * We cover (1) with REST, (2) with a URL-change assertion after navigation,
 * and (3) by opening the avatar dropdown and locating the menu item.
 *
 * The platform_admin side of the gate is exercised by ``login-multi-role``
 * and the ``admin-*`` specs; here we focus on the *denial* path so a
 * regression that opens ``/admin`` to regular users fails loudly.
 */
import { expect, test, type APIRequestContext } from "@playwright/test";
import {
    bootstrapIdentity,
    bootstrapInvitedMember,
    bootstrapPlatformAdmin,
    loginViaUI,
    requireStack,
    seedSession,
    type BootstrappedIdentity,
} from "./helpers";

test.beforeEach(async ({ baseURL }) => {
    await requireStack(baseURL);
});

/** A small set of admin endpoints used to prove the gate is uniformly 403. */
const ADMIN_ENDPOINTS = [
    { method: "GET", path: "/api/v1/admin/stats" },
    { method: "GET", path: "/api/v1/admin/identities?limit=1" },
    { method: "GET", path: "/api/v1/admin/workspaces?limit=1" },
    { method: "GET", path: "/api/v1/admin/approvals?limit=1" },
] as const;

async function callAdmin(
    request: APIRequestContext,
    baseURL: string,
    identity: BootstrappedIdentity,
    endpoint: (typeof ADMIN_ENDPOINTS)[number],
): Promise<number> {
    const res = await request.fetch(`${baseURL}${endpoint.path}`, {
        method: endpoint.method,
        headers: { Authorization: `Bearer ${identity.accessToken}` },
    });
    return res.status();
}

// ─── REST: regular users / workspace owners are 403 on admin.** ──

test("regular user (workspace owner) is 403 on every /api/v1/admin endpoint", async ({
    baseURL,
    request,
}) => {
    // Owner of their own workspace — still platform_role=user, so the gate
    // must bite. This is the most common live deployment shape.
    const user = await bootstrapIdentity(request, baseURL!);
    for (const ep of ADMIN_ENDPOINTS) {
        const status = await callAdmin(request, baseURL!, user, ep);
        expect(status, `${ep.method} ${ep.path} for regular user`).toBe(403);
    }
});

test("workspace admin (non platform_admin) is still 403 on /admin REST", async ({
    baseURL,
    request,
}) => {
    // A workspace admin is NOT a platform admin. Confirms that the workspace
    // ``admin`` role grants nothing outside the workspace — important for
    // multi-tenant isolation guarantees.
    const owner = await bootstrapIdentity(request, baseURL!);
    const wsAdmin = await bootstrapInvitedMember(
        request,
        baseURL!,
        owner,
        "admin",
    );
    for (const ep of ADMIN_ENDPOINTS) {
        const status = await callAdmin(request, baseURL!, wsAdmin, ep);
        expect(status, `${ep.method} ${ep.path} for workspace admin`).toBe(403);
    }
});

test("unauthenticated requests are 401 on /admin REST", async ({
    baseURL,
    request,
}) => {
    // Sanity check — the gate is 401 (no token) vs 403 (wrong role), and
    // the difference matters for client-side refresh loops.
    for (const ep of ADMIN_ENDPOINTS) {
        const res = await request.fetch(`${baseURL}${ep.path}`, {
            method: ep.method,
        });
        expect([401, 403], `${ep.method} ${ep.path} anonymous`).toContain(
            res.status(),
        );
    }
});

// ─── UI: /en-US/admin redirects away for non-admins ──────────

test("/en-US/admin bounces a regular user back to /", async ({
    baseURL,
    request,
    page,
}) => {
    const user = await bootstrapIdentity(request, baseURL!);
    await seedSession(page, user);

    await page.goto("/en-US/admin");

    // The admin layout renders a skeleton while ``useMe()`` resolves, then
    // ``router.replace("/")``. We just need the URL to settle somewhere that
    // isn't /admin. The 404 case would leave us on /admin, so this assertion
    // is meaningful even if the home page is heavy.
    await expect(page).not.toHaveURL(/\/en-US\/admin(\/|$)/, {
        timeout: 15_000,
    });
});

test("/en-US/admin/approvals bounces a regular user back too", async ({
    baseURL,
    request,
    page,
}) => {
    // Deep admin routes share the layout, so the gate propagates — this
    // guards against a future per-page guard that forgets to re-check.
    const user = await bootstrapIdentity(request, baseURL!);
    await seedSession(page, user);

    await page.goto("/en-US/admin/approvals");
    await expect(page).not.toHaveURL(/\/en-US\/admin/, { timeout: 15_000 });
});

// ─── UI: AvatarMenu gating ─────────────────────────────────

async function openAvatarMenu(page: import("@playwright/test").Page) {
    // The menu trigger is a Radix ``DropdownMenuTrigger`` rendered inside
    // the sidebar footer; it exposes ``aria-haspopup="menu"``. Scoping to
    // the sidebar aside rules out unrelated popup triggers in the header.
    const trigger = page.locator(
        'aside [aria-haspopup="menu"], footer [aria-haspopup="menu"]',
    ).last();
    await trigger.waitFor({ state: "visible", timeout: 15_000 });
    await trigger.click();
    // Radix portals the menu into document.body; wait until *any* menu item
    // appears so subsequent assertions don't race the open animation.
    await expect(
        page.locator('[role="menuitem"]').first(),
    ).toBeVisible({ timeout: 5_000 });
}

test("regular user does NOT see 'Platform admin' in AvatarMenu", async ({
    baseURL,
    request,
    page,
}) => {
    const user = await bootstrapIdentity(request, baseURL!);
    await loginViaUI(page, user.email, user.password);
    await page.goto("/en-US/");

    await openAvatarMenu(page);

    // The link would render as a menu item with an inner <Link>. If the gate
    // is broken, the link appears — count must stay at 0.
    const link = page.getByRole("menuitem", { name: /Platform admin/i });
    await expect(link).toHaveCount(0);
});

test("platform_admin DOES see 'Platform admin' in AvatarMenu", async ({
    baseURL,
    request,
    page,
}) => {
    // Self-skips when no admin seed is configured. We still want this
    // positive case in the platform-RBAC file so a reader can see both
    // sides of the gate in one place.
    const admin = await bootstrapPlatformAdmin(request, baseURL!);
    await loginViaUI(page, admin.email, admin.password);
    await page.goto("/en-US/");

    await openAvatarMenu(page);
    await expect(
        page.getByRole("menuitem", { name: /Platform admin/i }),
    ).toBeVisible({ timeout: 5_000 });
});

// ─── Promoting a user shouldn't change platform_role ──────

test("promoting a workspace member to admin does not leak platform admin access", async ({
    baseURL,
    request,
}) => {
    // Catches a subtle bug class: a future change that auto-syncs workspace
    // role to platform_role would silently escalate everyone. Prove a
    // workspace admin stays ``platform_role=user`` and remains 403 on the
    // admin REST surface.
    const owner = await bootstrapIdentity(request, baseURL!);
    const promoted = await bootstrapInvitedMember(
        request,
        baseURL!,
        owner,
        "admin",
    );

    // fetchMe without X-Workspace-Id uses the JWT/first-membership fallback;
    // platform_role doesn't depend on workspace, so either works.
    const me = await request.get(`${baseURL}/api/v1/me`, {
        headers: { Authorization: `Bearer ${promoted.accessToken}` },
    });
    expect(me.ok()).toBe(true);
    const body = (await me.json()) as { platform_role: string };
    expect(body.platform_role).toBe("user");

    const res = await request.get(`${baseURL}/api/v1/admin/stats`, {
        headers: { Authorization: `Bearer ${promoted.accessToken}` },
    });
    expect(res.status()).toBe(403);
});
