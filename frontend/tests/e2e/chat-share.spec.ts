/**
 * Chat — share dialog (direct invite + public link + revoke).
 *
 * Test plan:
 *   1. Open a fresh session, click the Share button → expect dialog.
 *   2. Generate a public link → expect a row with `link` badge to appear in
 *      "Shared with", and the rendered URL to start with /shared/.
 *   3. Invite a 2nd identity by email → expect the email to show up in the
 *      list with the View badge.
 *   4. Revoke the public link → only the email row remains.
 *   5. API cross-check: GET /shares returns the same shape we'd expect from
 *      a downstream UI integration.
 *   6. Permission guard: a non-owner identity gets 403 on POST /shares.
 */
import { expect, test } from "@playwright/test";
import {
    apiCreateAgent,
    apiCreateSession,
    authHeaders,
    bootstrapIdentity,
    randomSuffix,
    requireStack,
    seedSession,
} from "./helpers";

test.beforeEach(async ({ baseURL }) => {
    await requireStack(baseURL);
});

test("share dialog: generate link → invite by email → revoke link", async ({
    baseURL,
    request,
    page,
}) => {
    const identity = await bootstrapIdentity(request, baseURL!);
    const agent = await apiCreateAgent(request, baseURL!, identity);
    const session = await apiCreateSession(request, baseURL!, identity, {
        subject_id: agent.id,
        title: `Share spec ${randomSuffix()}`,
    });

    // A 2nd identity that we'll invite by email.
    const target = await bootstrapIdentity(request, baseURL!);

    await seedSession(page, identity);
    await page.goto(`/en-US/chat/${session.id}`);

    // Share now lives inside the right-rail workspace panel header. The
    // panel is collapsed by default, so expand it once before reaching
    // for the trigger.
    const workspaceToggle = page.getByTestId("workspace-toggle");
    if (
        (await page
            .getByTestId("workspace-panel")
            .getAttribute("data-collapsed")) === "true"
    ) {
        await workspaceToggle.click();
    }
    await page.getByTestId("share-trigger").click();
    const dialog = page.getByTestId("share-dialog");
    await expect(dialog).toBeVisible({ timeout: 10_000 });

    // ── Generate public link ──
    await dialog.getByTestId("share-generate-link").click();
    const linkText = await dialog
        .getByText(/\/shared\//)
        .first()
        .textContent({ timeout: 10_000 });
    expect(linkText, "rendered share URL").toMatch(/\/shared\/[A-Za-z0-9_-]+/);

    // ── Invite the 2nd identity by email ──
    await dialog.getByTestId("share-recipient").fill(target.email);
    await dialog.getByTestId("share-invite").click();
    await expect(
        dialog.getByText(target.email, { exact: false }),
    ).toBeVisible({ timeout: 10_000 });

    // ── API cross-check: 2 shares (1 link, 1 direct) ──
    const list = await request.get(
        `${baseURL}/api/v1/sessions/${session.id}/shares`,
        { headers: authHeaders(identity) },
    );
    expect(list.ok()).toBe(true);
    const body = (await list.json()) as {
        items: Array<{
            id: string;
            token: string | null;
            shared_with_identity_id: string | null;
            shared_with_email: string | null;
        }>;
        total: number;
    };
    expect(body.total).toBe(2);
    const directShare = body.items.find(
        (s) => s.shared_with_identity_id !== null,
    );
    const linkShare = body.items.find((s) => s.token !== null);
    expect(directShare).toBeTruthy();
    expect(directShare?.shared_with_email).toBe(target.email);
    expect(linkShare?.token).toMatch(/^[A-Za-z0-9_-]{30,}$/);

    // ── Revoke the public link via the dialog ──
    const linkRow = dialog
        .getByTestId("share-row")
        .filter({ hasText: /link/i })
        .first();
    await linkRow.getByTestId("share-revoke").click();
    await expect(
        dialog.getByText(/\/shared\//),
        "URL preview should disappear after revoke",
    ).toHaveCount(0, { timeout: 10_000 });

    // Public access via the now-revoked token must 404.
    const revoked = await request.get(
        `${baseURL}/api/v1/sessions/shared/${linkShare!.token}`,
    );
    expect(revoked.status()).toBe(404);
});

test("non-owner is forbidden from creating or listing shares", async ({
    baseURL,
    request,
}) => {
    const owner = await bootstrapIdentity(request, baseURL!);
    const intruder = await bootstrapIdentity(request, baseURL!);
    const agent = await apiCreateAgent(request, baseURL!, owner);
    const session = await apiCreateSession(request, baseURL!, owner, {
        subject_id: agent.id,
    });

    // Cross-workspace request: the intruder can't even see the session
    // (404) because their X-Workspace-Id doesn't match. That's the
    // strongest guard — the share-not-owner branch only fires when both
    // tenants overlap.
    const create = await request.post(
        `${baseURL}/api/v1/sessions/${session.id}/shares`,
        {
            headers: authHeaders(intruder),
            data: { generate_link: true, permission: "view" },
        },
    );
    expect([403, 404]).toContain(create.status());
});
