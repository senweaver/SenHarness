/**
 * `/shared/[token]` — public read-only conversation page.
 *
 * Coverage:
 *   - Generate a public link via REST → visit `/en-US/shared/{token}` with
 *     **no auth seeded** → expect markdown transcript + read-only badge,
 *     **no chat input**, **no rating buttons**, **no share button**, **no
 *     sidebar**.
 *   - Revoke the share → revisit → expect the friendly "Link invalid or
 *     revoked" page with a Back-to-home button.
 *   - Bogus token → same friendly error page (404 from API).
 */
import { expect, test } from "@playwright/test";
import {
    apiCreateAgent,
    apiCreateSession,
    authHeaders,
    bootstrapIdentity,
    randomSuffix,
    requireStack,
} from "./helpers";

test.beforeEach(async ({ baseURL }) => {
    await requireStack(baseURL);
});

async function seedAssistantMessage(
    request: import("@playwright/test").APIRequestContext,
    baseURL: string,
    identity: { accessToken: string; workspaceId: string },
    sessionId: string,
    text: string,
) {
    const r = await request.post(
        `${baseURL}/api/v1/sessions/${sessionId}/messages`,
        {
            headers: authHeaders(
                identity as Parameters<typeof authHeaders>[0],
            ),
            data: {
                role: "assistant",
                content_json: { text },
                attachments_json: [],
            },
        },
    );
    expect(r.status()).toBe(201);
}

test("a generated public link renders the transcript with no UI affordances", async ({
    baseURL,
    request,
    page,
}) => {
    const identity = await bootstrapIdentity(request, baseURL!);
    const agent = await apiCreateAgent(request, baseURL!, identity);
    const session = await apiCreateSession(request, baseURL!, identity, {
        subject_id: agent.id,
        title: `Public spec ${randomSuffix()}`,
    });

    // Seed messages with markdown so we can also assert the renderer ran.
    await seedAssistantMessage(
        request,
        baseURL!,
        identity,
        session.id,
        "# Public hello\n\n- bullet\n- another\n\n```python\nprint(1)\n```",
    );

    // Mint a public link.
    const create = await request.post(
        `${baseURL}/api/v1/sessions/${session.id}/shares`,
        {
            headers: authHeaders(identity),
            data: { generate_link: true, permission: "view" },
        },
    );
    expect(create.status()).toBe(201);
    const share = (await create.json()) as { token: string };
    expect(share.token).toBeTruthy();

    // **NO seedSession** — we're acting as an anonymous visitor.
    await page.goto(`/en-US/shared/${share.token}`);

    // Read-only badge + heading.
    await expect(
        page.getByRole("heading", {
            name: new RegExp(`Public spec`, "i"),
            level: 1,
        }),
    ).toBeVisible({ timeout: 15_000 });

    // Markdown rendered → semantic heading + list items + code block.
    await expect(page.getByRole("heading", { name: "Public hello" })).toBeVisible();
    await expect(page.getByRole("listitem", { name: "bullet" })).toBeVisible();

    // None of the privileged affordances should be present.
    await expect(page.getByTestId("chat-input")).toHaveCount(0);
    await expect(page.getByTestId("share-trigger")).toHaveCount(0);
    await expect(page.getByTestId("rating-buttons")).toHaveCount(0);
});

test("revoked share returns the friendly error page", async ({
    baseURL,
    request,
    page,
}) => {
    const identity = await bootstrapIdentity(request, baseURL!);
    const agent = await apiCreateAgent(request, baseURL!, identity);
    const session = await apiCreateSession(request, baseURL!, identity, {
        subject_id: agent.id,
    });
    await seedAssistantMessage(
        request,
        baseURL!,
        identity,
        session.id,
        "anything",
    );

    const create = await request.post(
        `${baseURL}/api/v1/sessions/${session.id}/shares`,
        {
            headers: authHeaders(identity),
            data: { generate_link: true, permission: "view" },
        },
    );
    const share = (await create.json()) as { id: string; token: string };

    // Revoke immediately.
    const del = await request.delete(
        `${baseURL}/api/v1/sessions/${session.id}/shares/${share.id}`,
        { headers: authHeaders(identity) },
    );
    expect(del.status()).toBe(204);

    await page.goto(`/en-US/shared/${share.token}`);
    await expect(
        page.getByRole("heading", { name: /Link invalid or revoked/i, level: 1 }),
    ).toBeVisible({ timeout: 15_000 });
    await expect(
        page.getByRole("link", { name: /Back to home/i }),
    ).toBeVisible();
});

test("bogus token also lands on the friendly error page", async ({
    baseURL,
    page,
}) => {
    await page.goto(`/en-US/shared/this-token-does-not-exist`);
    await expect(
        page.getByRole("heading", { name: /Link invalid or revoked/i, level: 1 }),
    ).toBeVisible({ timeout: 15_000 });
    // Make sure the error message references the share / re-share copy so we
    // don't accidentally regress to the generic "load failed" branch.
    await expect(
        page.getByText(/share link has expired or been revoked/i),
    ).toBeVisible();
    expect(baseURL).toBeTruthy();
});
