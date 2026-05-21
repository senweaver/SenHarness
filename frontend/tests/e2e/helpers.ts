/**
 * Shared helpers for E2E specs.
 *
 * V2 strategy — Hybrid:
 *   - account creation / workspace bootstrap use the REST API (avoids
 *     brittle UI flakes for plumbing steps);
 *   - the **critical product flows** (login form, agent create form, first
 *     chat with tool call) drive real UI clicks so a regression in the
 *     front-end actually breaks the test.
 *
 * `requireStack` short-circuits specs when the dev stack isn't reachable —
 * preferable to letting Playwright timeout every test on a fresh clone.
 *
 * ─── Selector priority (used across all specs) ──────────────────────
 *   1. Existing `data-testid`         — most stable, prefer when present.
 *   2. `getByRole('heading'|'button', { name })` — semantic anchors from
 *      the English catalogue at `frontend/messages/en-US.json`.
 *   3. `getByLabel` / `getByPlaceholder` — for form fields with stable
 *      accessible names.
 *   4. URL regex  — e.g. `toHaveURL(/\/en-US\/agents\/[0-9a-f-]{36}$/)`
 *      as a cheap post-redirect assertion.
 *   5. Static English copy via `getByText` — last resort.
 *   6. Add a new `data-testid` **only** when none of the above can pin a
 *      unique element (reserved for `SquadForm`, `FlowForm`, invite page).
 *
 * Always drive the UI through the locale prefix (``/en-US/…``) — routes
 * without a prefix are re-wrapped by the middleware and can surprise tests
 * that race the locale rewrite.
 */
import { APIRequestContext, Page, test, expect } from "@playwright/test";

// ──────────────────────────────────────────────────────────
// Random identifiers
// ──────────────────────────────────────────────────────────

/** Convenience — a random identity that won't collide across runs. */
export function randomEmail(): string {
    return `e2e-${Date.now()}-${Math.random().toString(36).slice(2, 8)}@example.com`;
}

/** Same, for workspace slugs (slugs must be unique per platform). */
export function randomSlug(prefix = "e2e"): string {
    return `${prefix}-${Date.now()}-${Math.random().toString(36).slice(2, 6)}`;
}

/** Short, human-readable suffix for entity names.
 *  Uses 6 random chars (instead of 3) to reduce collision probability
 *  across many test runs against a shared database. */
export function randomSuffix(): string {
    return `${Date.now().toString(36)}${Math.random().toString(36).slice(2, 8)}`;
}

// ──────────────────────────────────────────────────────────
// Stack availability
// ──────────────────────────────────────────────────────────

/**
 * Bail out of a spec early when the backend isn't reachable.
 *
 * Returns the base URL so the caller doesn't have to null-assert `baseURL!`.
 */
export async function requireStack(baseURL: string | undefined): Promise<string> {
    if (!baseURL) {
        test.skip(true, "no baseURL configured — set PLAYWRIGHT_BASE_URL");
    }
    const res = await fetch(`${baseURL}/api/v1/health`).catch(() => null);
    if (!res || !res.ok) {
        test.skip(
            true,
            `SenHarness backend not reachable at ${baseURL}/api/v1/health; ` +
                "start the stack with `docker compose up -d` (or equivalent).",
        );
    }
    return baseURL!;
}

// ──────────────────────────────────────────────────────────
// Identity + workspace bootstrap
// ──────────────────────────────────────────────────────────

export interface BootstrappedIdentity {
    email: string;
    password: string;
    accessToken: string;
    identityId: string;
    workspaceId: string;
    workspaceSlug: string;
}

/**
 * Back-compat alias for older smoke specs (governance-smoke,
 * memory-soul-smoke). Prefer `bootstrapIdentity()` in new code.
 */
export const bootstrapAccount = (
    request: APIRequestContext,
    baseURL: string,
): Promise<BootstrappedIdentity> => bootstrapIdentity(request, baseURL);

/**
 * Back-compat alias — seeds the Zustand stores in the given page. Accepts
 * the extra ``baseURL`` arg that older smoke specs passed so they keep
 * compiling. New code should call ``seedSession(page, identity)`` directly.
 */
export async function persistBootstrappedAuth(
    page: Page,
    identity: BootstrappedIdentity,
    _baseURL?: string,
): Promise<void> {
    await seedSession(page, identity);
}

/**
 * Register + login + create a workspace in one round-trip. Returns the
 * access token + workspace id so UI specs can seed the browser storage
 * without clicking through register/login pages.
 */
export async function bootstrapIdentity(
    request: APIRequestContext,
    baseURL: string,
): Promise<BootstrappedIdentity> {
    const email = randomEmail();
    const password = "e2e-password-very-long";

    const register = await request.post(`${baseURL}/api/v1/auth/register`, {
        data: { email, name: "E2E User", password },
    });
    expect(register.status(), "register response").toBe(201);
    // /auth/register returns ``IdentityRead`` whose primary key is ``id``.
    // We rename locally to match the ``identityId`` field exposed by
    // ``BootstrappedIdentity`` so downstream specs can PATCH members.
    const { id: identityId } = (await register.json()) as { id: string };

    const login = await request.post(`${baseURL}/api/v1/auth/login`, {
        data: { email, password },
    });
    expect(login.ok(), "login response").toBe(true);
    const { access_token: accessToken } = (await login.json()) as {
        access_token: string;
    };

    const slug = randomSlug("e2e-ws");
    const createWs = await request.post(`${baseURL}/api/v1/workspaces`, {
        headers: { Authorization: `Bearer ${accessToken}` },
        data: {
            name: "E2E Test Co",
            slug,
            description: "created by e2e helper bootstrapIdentity()",
        },
    });
    expect([200, 201], "workspace create status").toContain(createWs.status());
    const { id: workspaceId } = (await createWs.json()) as { id: string };

    return { email, password, accessToken, identityId, workspaceId, workspaceSlug: slug };
}

/**
 * Log in as an **existing** identity via the REST API. Used by the optional
 * `bootstrapPlatformAdmin()` path where the admin account is seeded outside
 * the test (env vars, `make seed`, etc.).
 */
export async function loginExistingIdentity(
    request: APIRequestContext,
    baseURL: string,
    email: string,
    password: string,
): Promise<{ accessToken: string; identityId: string }> {
    const login = await request.post(`${baseURL}/api/v1/auth/login`, {
        data: { email, password },
    });
    if (!login.ok()) {
        throw new Error(
            `login failed for ${email}: ${login.status()} ${await login.text()}`,
        );
    }
    const { access_token: accessToken } = (await login.json()) as {
        access_token: string;
    };
    // Note: the backend mounts ``me`` at ``/api/v1/me`` (not under ``/auth``).
    // An earlier draft of this helper hit ``/api/v1/auth/me`` and always 404'd,
    // which made ``bootstrapPlatformAdmin`` silently skip every admin spec.
    const me = await request.get(`${baseURL}/api/v1/me`, {
        headers: { Authorization: `Bearer ${accessToken}` },
    });
    if (!me.ok()) {
        throw new Error(
            `/api/v1/me failed for ${email}: ${me.status()} ${await me.text()}`,
        );
    }
    const { id: identityId } = (await me.json()) as { id: string };
    return { accessToken, identityId };
}

/**
 * Try to acquire a platform_admin identity for admin-only specs.
 *
 * Priority:
 *   1. ``E2E_PLATFORM_ADMIN_EMAIL`` + ``E2E_PLATFORM_ADMIN_PASSWORD`` env
 *      vars — login + verify ``/auth/me`` returns
 *      ``platform_role === "platform_admin"``.
 *   2. Otherwise, skip the spec with a clear explanation — admin coverage
 *      requires an out-of-band seed step (the backend has no self-promote
 *      endpoint by design).
 *
 * Returns a ``BootstrappedIdentity``-shaped object so specs can reuse
 * ``seedSession`` and the generic API factories below.
 */
export async function bootstrapPlatformAdmin(
    request: APIRequestContext,
    baseURL: string,
): Promise<BootstrappedIdentity> {
    const email = process.env.E2E_PLATFORM_ADMIN_EMAIL;
    const password = process.env.E2E_PLATFORM_ADMIN_PASSWORD;
    if (!email || !password) {
        test.skip(
            true,
            "no platform_admin seed available — set " +
                "E2E_PLATFORM_ADMIN_EMAIL + E2E_PLATFORM_ADMIN_PASSWORD to run " +
                "admin-* specs (the backend exposes no self-promote endpoint).",
        );
    }

    let accessToken: string;
    let identityId: string;
    try {
        const res = await loginExistingIdentity(request, baseURL, email!, password!);
        accessToken = res.accessToken;
        identityId = res.identityId;
    } catch (err) {
        test.skip(true, `platform_admin login failed: ${(err as Error).message}`);
        return {} as never;
    }

    const me = await request.get(`${baseURL}/api/v1/me`, {
        headers: { Authorization: `Bearer ${accessToken}` },
    });
    const body = (await me.json()) as { platform_role?: string };
    if (body.platform_role !== "platform_admin") {
        test.skip(
            true,
            `identity ${email} is not platform_admin (got ${body.platform_role ?? "unknown"}). ` +
                "Promote via DB or seed script before running admin-* specs.",
        );
    }

    // Admin specs still need an X-Workspace-Id for the non-admin endpoints
    // they touch incidentally (e.g. loading the shell). Reuse the first
    // workspace the admin is a member of, or create a throwaway one.
    const wsList = await request.get(`${baseURL}/api/v1/workspaces`, {
        headers: { Authorization: `Bearer ${accessToken}` },
    });
    let workspaceId = "";
    let workspaceSlug = "";
    if (wsList.ok()) {
        const arr = (await wsList.json()) as Array<{ id: string; slug: string }>;
        if (arr.length > 0) {
            workspaceId = arr[0]!.id;
            workspaceSlug = arr[0]!.slug;
        }
    }
    if (!workspaceId) {
        const slug = randomSlug("e2e-admin-ws");
        const create = await request.post(`${baseURL}/api/v1/workspaces`, {
            headers: { Authorization: `Bearer ${accessToken}` },
            data: {
                name: "E2E Admin WS",
                slug,
                description: "scratch workspace for admin specs",
            },
        });
        if (create.ok()) {
            const body = (await create.json()) as { id: string; slug: string };
            workspaceId = body.id;
            workspaceSlug = body.slug;
        }
    }

    return {
        email: email!,
        password: password!,
        accessToken,
        identityId,
        workspaceId,
        workspaceSlug,
    };
}

/**
 * Seed the Zustand auth + workspace stores in the page context so the
 * Next.js app behaves as if the user just completed login. Avoids the UI
 * login dance for specs that want to test something after login.
 *
 * Must be called **before** the first navigation that hits the guarded
 * part of the app (or the redirect to /login races the seed).
 */
export async function seedSession(
    page: Page,
    identity: BootstrappedIdentity,
): Promise<void> {
    const expiresAt = new Date(Date.now() + 30 * 60_000).toISOString();
    await page.addInitScript(
        ([token, ws, exp]) => {
            const authState = {
                state: {
                    accessToken: token,
                    accessExpiresAt: exp,
                    identityId: null,
                },
                version: 0,
            };
            const wsState = {
                state: { activeWorkspaceId: ws },
                version: 0,
            };
            try {
                localStorage.setItem("senharness.auth", JSON.stringify(authState));
                localStorage.setItem(
                    "senharness.workspace",
                    JSON.stringify(wsState),
                );
                // Suppress the OnboardingTour dialog so it doesn't apply
                // aria-hidden to the page and break getByRole() queries.
                localStorage.setItem("senharness:onboarding:v1", "done");
            } catch {
                // localStorage unavailable — the spec will hit the login page
                // and should fall back to the UI flow instead.
            }
        },
        [identity.accessToken, identity.workspaceId, expiresAt] as const,
    );
}

/** Alias that reads better in admin specs. */
export const seedAdminSession = seedSession;

/**
 * Real UI sign-in. Types into the `data-testid`-tagged email/password fields,
 * clicks submit, and waits for the post-login redirect. Use when the spec
 * specifically tests the login form.
 */
export async function loginViaUI(
    page: Page,
    email: string,
    password: string,
): Promise<void> {
    // Suppress the OnboardingTour dialog before any navigation so it doesn't
    // apply aria-hidden to the page and break getByRole() queries post-login.
    await page.addInitScript(() => {
        try {
            localStorage.setItem("senharness:onboarding:v1", "done");
        } catch {
            // ignore
        }
    });
    await page.goto("/en-US/login");
    await page.getByTestId("login-email").fill(email);
    await page.getByTestId("login-password").fill(password);
    await page.getByTestId("login-submit").click();
    // After the redirect, Next.js lands on `/` (locale-prefixed) once the
    // session is stored. The `/` route shows the Home screen for logged-in
    // users; we assert the URL changed off /login.
    await expect(page).not.toHaveURL(/\/login/, { timeout: 15_000 });
}

// ──────────────────────────────────────────────────────────
// API data factories
// ──────────────────────────────────────────────────────────

/** Common auth + workspace-scoped headers for REST calls on behalf of a bootstrapped user. */
export function authHeaders(identity: BootstrappedIdentity): Record<string, string> {
    return {
        Authorization: `Bearer ${identity.accessToken}`,
        "X-Workspace-Id": identity.workspaceId,
    };
}

export interface ApiAgentCreateOverrides {
    name?: string;
    description?: string;
    persona_md?: string;
    backend_kind?: string;
    visibility?: string;
    autonomy_level?: string;
    metadata_json?: Record<string, unknown>;
}

/**
 * Create an agent via API. Returns `{ id, name }` for downstream assertions.
 * Uses the same ``approvals:false`` / ``sandbox:"state"`` defaults as
 * `first-chat.spec.ts` to keep e2e cycles tight.
 */
export async function apiCreateAgent(
    request: APIRequestContext,
    baseURL: string,
    identity: BootstrappedIdentity,
    overrides: ApiAgentCreateOverrides = {},
): Promise<{ id: string; name: string }> {
    const name = overrides.name ?? `E2E Agent ${randomSuffix()}`;
    const res = await request.post(`${baseURL}/api/v1/agents`, {
        headers: authHeaders(identity),
        data: {
            name,
            description: overrides.description ?? "e2e api factory agent",
            persona_md:
                overrides.persona_md ??
                "You are a concise assistant. Reply with the result only.",
            backend_kind: overrides.backend_kind ?? "native",
            visibility: overrides.visibility ?? "private",
            autonomy_level: overrides.autonomy_level ?? "l2",
            metadata_json: overrides.metadata_json ?? {
                approvals: false,
                sandbox: "state",
            },
        },
    });
    if (!res.ok()) {
        throw new Error(
            `apiCreateAgent failed: ${res.status()} ${await res.text()}`,
        );
    }
    const agent = (await res.json()) as { id: string; name: string };
    return { id: agent.id, name: agent.name };
}

export interface ApiSquadCreateOverrides {
    name?: string;
    description?: string | null;
    strategy?: "router" | "planner" | "worker_pool" | "handoff" | "debate";
    members?: Array<{ agent_id: string; role_in_squad?: string; weight?: number }>;
}

export async function apiCreateSquad(
    request: APIRequestContext,
    baseURL: string,
    identity: BootstrappedIdentity,
    overrides: ApiSquadCreateOverrides = {},
): Promise<{ id: string; name: string }> {
    const name = overrides.name ?? `E2E Squad ${randomSuffix()}`;
    const members = (overrides.members ?? []).map((m, idx) => ({
        agent_id: m.agent_id,
        role_in_squad: m.role_in_squad ?? "member",
        weight: m.weight ?? idx,
    }));
    const res = await request.post(`${baseURL}/api/v1/squads`, {
        headers: authHeaders(identity),
        data: {
            name,
            description: overrides.description ?? "e2e api factory squad",
            strategy: overrides.strategy ?? "router",
            members,
        },
    });
    if (!res.ok()) {
        throw new Error(
            `apiCreateSquad failed: ${res.status()} ${await res.text()}`,
        );
    }
    const squad = (await res.json()) as { id: string; name: string };
    return { id: squad.id, name: squad.name };
}

export interface ApiFlowCreateOverrides {
    name?: string;
    description?: string | null;
    trigger_kind?: "cron" | "webhook" | "manual";
    trigger_config?: Record<string, unknown>;
    agent_id: string;
    prompt_template?: string;
    enabled?: boolean;
}

export async function apiCreateFlow(
    request: APIRequestContext,
    baseURL: string,
    identity: BootstrappedIdentity,
    overrides: ApiFlowCreateOverrides,
): Promise<{ id: string; name: string }> {
    const name = overrides.name ?? `E2E Flow ${randomSuffix()}`;
    const res = await request.post(`${baseURL}/api/v1/flows`, {
        headers: authHeaders(identity),
        data: {
            name,
            description: overrides.description ?? "e2e api factory flow",
            trigger_kind: overrides.trigger_kind ?? "manual",
            trigger_config: overrides.trigger_config ?? {},
            agent_id: overrides.agent_id,
            prompt_template:
                overrides.prompt_template ?? "Summarise today's status briefly.",
            enabled: overrides.enabled ?? true,
        },
    });
    if (!res.ok()) {
        throw new Error(
            `apiCreateFlow failed: ${res.status()} ${await res.text()}`,
        );
    }
    const flow = (await res.json()) as { id: string; name: string };
    return { id: flow.id, name: flow.name };
}

export interface ApiSessionCreateOverrides {
    kind?: "p2p" | "group";
    title?: string;
    subject_id: string;
}

export async function apiCreateSession(
    request: APIRequestContext,
    baseURL: string,
    identity: BootstrappedIdentity,
    overrides: ApiSessionCreateOverrides,
): Promise<{ id: string }> {
    const res = await request.post(`${baseURL}/api/v1/sessions`, {
        headers: authHeaders(identity),
        data: {
            kind: overrides.kind ?? "p2p",
            subject_id: overrides.subject_id,
            title: overrides.title ?? `E2E session ${randomSuffix()}`,
        },
    });
    if (!res.ok()) {
        throw new Error(
            `apiCreateSession failed: ${res.status()} ${await res.text()}`,
        );
    }
    const s = (await res.json()) as { id: string };
    return { id: s.id };
}

export interface ApiKnowledgeCollectionOverrides {
    name?: string;
    description?: string | null;
    config_json?: Record<string, unknown>;
}

export async function apiCreateKnowledgeCollection(
    request: APIRequestContext,
    baseURL: string,
    identity: BootstrappedIdentity,
    overrides: ApiKnowledgeCollectionOverrides = {},
): Promise<{ id: string; name: string }> {
    const name = overrides.name ?? `E2E KB ${randomSuffix()}`;
    const res = await request.post(`${baseURL}/api/v1/knowledge/collections`, {
        headers: authHeaders(identity),
        data: {
            name,
            description: overrides.description ?? "e2e api factory collection",
            config_json: overrides.config_json ?? {},
        },
    });
    if (!res.ok()) {
        throw new Error(
            `apiCreateKnowledgeCollection failed: ${res.status()} ${await res.text()}`,
        );
    }
    const c = (await res.json()) as { id: string; name: string };
    return { id: c.id, name: c.name };
}

// ──────────────────────────────────────────────────────────
// UI smoke helper
// ──────────────────────────────────────────────────────────

/**
 * Navigate to ``url`` and assert the main ``<h1>`` is visible. ``heading``
 * may be an exact string or a regex — pass a regex whenever the copy is
 * templated (e.g. `/memory/i`). This is the backbone of data-driven smoke
 * tests that walk the settings / admin trees.
 */
export async function gotoAndExpectH1(
    page: Page,
    url: string,
    heading: string | RegExp,
    timeout = 20_000,
): Promise<void> {
    await page.goto(url, { waitUntil: "domcontentloaded" });
    await expect(page.getByRole("heading", { name: heading, level: 1 })).toBeVisible({
        timeout,
    });
}

/** Small wait used to let React Query refetches settle before UI assertions. */
export async function waitForNetworkIdle(page: Page, ms = 500): Promise<void> {
    await page.waitForLoadState("networkidle").catch(() => {});
    await page.waitForTimeout(ms);
}

// ──────────────────────────────────────────────────────────
// Multi-role helpers (used by login-multi-role / rbac-* specs)
// ──────────────────────────────────────────────────────────

/**
 * Built-in workspace roles that every workspace ships with. See
 * ``backend/app/db/models/role.py::BuiltinRole`` — keep this list in sync
 * if new roles are added server-side.
 */
export const WORKSPACE_ROLES = [
    "owner",
    "admin",
    "operator",
    "member",
    "auditor",
    "guest",
] as const;

export type WorkspaceRole = (typeof WORKSPACE_ROLES)[number];

/**
 * Subset of ``/api/v1/me`` fields needed by the RBAC specs. The backend
 * returns more; we pick the role-relevant ones so type drift elsewhere
 * (e.g. new profile fields) doesn't break these specs.
 */
export interface MeSnapshot {
    id: string;
    email: string;
    platform_role: "user" | "platform_admin";
    current_workspace_id: string | null;
    current_role: string | null;
    permissions: string[];
    workspaces: Array<{
        workspace_id: string;
        workspace_slug: string;
        role: string;
    }>;
}

/**
 * Hit ``GET /api/v1/me`` and return the role snapshot. ``workspaceId`` is
 * sent as ``X-Workspace-Id`` so ``current_role`` / ``permissions`` reflect
 * that workspace (without the header the backend would fall back to the
 * JWT's ``ws`` claim or the user's first membership).
 */
export async function fetchMe(
    request: APIRequestContext,
    baseURL: string,
    accessToken: string,
    workspaceId?: string,
): Promise<MeSnapshot> {
    const headers: Record<string, string> = {
        Authorization: `Bearer ${accessToken}`,
    };
    if (workspaceId) headers["X-Workspace-Id"] = workspaceId;
    const res = await request.get(`${baseURL}/api/v1/me`, { headers });
    if (!res.ok()) {
        throw new Error(`GET /me failed: ${res.status()} ${await res.text()}`);
    }
    return (await res.json()) as MeSnapshot;
}

/**
 * Bootstrap a second identity and join the given ``owner``'s workspace as
 * ``role``. Flow: register → login → owner invites by email → invitee
 * accepts the code. For custom roles (``admin`` / ``operator`` / ``auditor``
 * / ``guest``) we issue the invite with ``role="member"`` first and then
 * ``PATCH`` the membership — the invitation schema accepts any string but
 * the server only honours the builtin set via ``ROLE_CAPABILITIES`` lookup.
 *
 * The returned ``BootstrappedIdentity`` points at the **owner's** workspace
 * (not the invitee's own throwaway workspace they created at registration),
 * so ``seedSession`` drops the invitee straight into the shared workspace.
 */
export async function bootstrapInvitedMember(
    request: APIRequestContext,
    baseURL: string,
    owner: BootstrappedIdentity,
    role: WorkspaceRole,
): Promise<BootstrappedIdentity> {
    // Register the invitee — this also creates their own "personal" workspace
    // as a side effect of bootstrapIdentity(), which we ignore because we
    // re-point ``workspaceId`` at the owner's workspace below.
    const invitee = await bootstrapIdentity(request, baseURL);

    const invite = await request.post(
        `${baseURL}/api/v1/workspaces/${owner.workspaceId}/invitations`,
        {
            headers: authHeaders(owner),
            data: {
                email: invitee.email,
                // Issue with the canonical ``member`` role for the initial
                // accept, then promote below — matches how the product
                // onboards non-default roles (owner manually adjusts after
                // accept).
                role: "member",
                expires_in_hours: 24,
            },
        },
    );
    if (invite.status() !== 201) {
        throw new Error(
            `create invitation failed: ${invite.status()} ${await invite.text()}`,
        );
    }
    const { code } = (await invite.json()) as { code: string };

    const accept = await request.post(
        `${baseURL}/api/v1/workspaces/invitations/accept`,
        {
            headers: {
                Authorization: `Bearer ${invitee.accessToken}`,
            },
            data: { code },
        },
    );
    if (!accept.ok()) {
        throw new Error(
            `accept invitation failed: ${accept.status()} ${await accept.text()}`,
        );
    }

    if (role !== "member") {
        await updateMemberRole(
            request,
            baseURL,
            owner,
            invitee.identityId,
            role,
        );
    }

    return {
        ...invitee,
        workspaceId: owner.workspaceId,
        workspaceSlug: owner.workspaceSlug,
    };
}

/**
 * PATCH a workspace member's role. The ``identity_target`` path param is
 * the invitee's identity id (confirmed in ``workspaces.py::update_member``).
 */
export async function updateMemberRole(
    request: APIRequestContext,
    baseURL: string,
    owner: BootstrappedIdentity,
    memberIdentityId: string,
    role: WorkspaceRole,
): Promise<void> {
    const res = await request.patch(
        `${baseURL}/api/v1/workspaces/${owner.workspaceId}/members/${memberIdentityId}`,
        {
            headers: authHeaders(owner),
            data: { role },
        },
    );
    if (!res.ok()) {
        throw new Error(
            `promote to ${role} failed: ${res.status()} ${await res.text()}`,
        );
    }
}
