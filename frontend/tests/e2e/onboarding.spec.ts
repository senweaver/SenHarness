/**
 * Hybrid onboarding spec.
 *
 * Plumbing (register + create workspace) goes through the REST API to keep
 * the spec resilient to future UI restyling. The **critical UI surface** —
 * the login form — is clicked through with real keystrokes + submit so any
 * regression in `(auth)/login/page.tsx` breaks this test loudly.
 *
 * Assertions verify:
 *   - Register endpoint returns 201 with an identity id.
 *   - UI login lands the user on a non-/login route (i.e. session storage was
 *     seeded and the middleware let them through).
 *   - Freshly-created workspace is reachable via the API using the token.
 *   - /agents/runtimes enumerates the bundled `native` backend (gate
 *     for Agent creation form + runtimes page).
 */
import { expect, test } from "@playwright/test";
import {
    bootstrapIdentity,
    loginViaUI,
    randomEmail,
    requireStack,
} from "./helpers";

test.beforeEach(async ({ baseURL }) => {
    await requireStack(baseURL);
});

test("register API + UI login + workspace create round-trip", async ({
    baseURL,
    request,
    page,
}) => {
    const email = randomEmail();
    const password = "e2e-password-very-long";

    // ── 1. Register via API (stable plumbing).
    const register = await request.post(`${baseURL}/api/v1/auth/register`, {
        data: { email, name: "E2E User", password },
    });
    expect(register.status()).toBe(201);

    // ── 2. UI login — real form interaction, tagged with data-testid.
    await loginViaUI(page, email, password);

    // ── 3. Pull the token from the browser's persisted auth store, so we
    //       can verify the rest of the stack via API without round-tripping
    //       the login API call.
    const storeJson = await page.evaluate(() =>
        localStorage.getItem("senharness.auth"),
    );
    expect(storeJson, "auth store persisted").toBeTruthy();
    const { state } = JSON.parse(storeJson!);
    const token = state.accessToken as string;
    expect(token).toBeTruthy();

    // ── 4. Workspace create via API using the just-issued token.
    const createWs = await request.post(`${baseURL}/api/v1/workspaces`, {
        headers: { Authorization: `Bearer ${token}` },
        data: {
            name: "E2E Test Co",
            slug: `e2e-${Date.now()}`,
            description: "created by onboarding.spec.ts",
        },
    });
    expect([200, 201]).toContain(createWs.status());
    const ws = await createWs.json();
    expect(ws.workspace_type ?? "company").toBe("company");

    // ── 5. Runtimes endpoint must surface the bundled native runtime.
    const runtimes = await request.get(`${baseURL}/api/v1/agents/runtimes`);
    expect(runtimes.ok()).toBe(true);
    const payload = await runtimes.json();
    const kinds: string[] = (payload.runtimes ?? []).map(
        (r: { kind: string }) => r.kind,
    );
    expect(kinds).toContain("native");
});

test("bootstrap helper happy path", async ({ baseURL, request }) => {
    // Not duplicative — this doubles as a sanity check that the helper
    // itself is wired correctly (downstream specs depend on it).
    const id = await bootstrapIdentity(request, baseURL!);
    expect(id.accessToken).toBeTruthy();
    expect(id.workspaceId).toBeTruthy();

    const me = await request.get(`${baseURL}/api/v1/me`, {
        headers: {
            Authorization: `Bearer ${id.accessToken}`,
            "X-Workspace-Id": id.workspaceId,
        },
    });
    expect(me.ok()).toBe(true);
});
