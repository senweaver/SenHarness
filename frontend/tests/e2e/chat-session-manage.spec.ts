/**
 * Chat session management — rename + delete via API, with UI
 * verification.
 *
 * The frontend doesn't yet expose a user-visible "rename session" button
 * on the chat page (sessions are titled automatically from the first
 * prompt), so we drive the rename API directly and confirm the chat page
 * mount doesn't 5xx. Delete lives under the sidebar / recent sessions
 * flows; here we assert the REST round-trip is consistent with the
 * list/detail endpoints the UI relies on.
 *
 * (`chat-checkpoint-fork` in the plan was speculative — the backend
 * currently exposes no `/sessions/{id}/checkpoints` or `/fork` routes,
 * so we cover the session-lifecycle operations that actually exist.)
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

test("rename session via API, then open it in the UI", async ({
    baseURL,
    request,
    page,
}) => {
    const identity = await bootstrapIdentity(request, baseURL!);
    const agent = await apiCreateAgent(request, baseURL!, identity);
    const session = await apiCreateSession(request, baseURL!, identity, {
        subject_id: agent.id,
        title: `initial ${randomSuffix()}`,
    });

    const renamed = `Renamed session ${randomSuffix()}`;
    const patch = await request.patch(
        `${baseURL}/api/v1/sessions/${session.id}`,
        {
            headers: authHeaders(identity),
            data: { title: renamed },
        },
    );
    expect(patch.ok(), "rename response").toBe(true);

    await seedSession(page, identity);
    await page.goto(`/en-US/chat/${session.id}`);
    await expect(page.getByTestId("chat-input")).toBeVisible({
        timeout: 15_000,
    });
    // API list should now carry the new title.
    const list = await request.get(`${baseURL}/api/v1/sessions`, {
        headers: authHeaders(identity),
    });
    const titles = ((await list.json()) as Array<{ title: string | null }>)
        .map((s) => s.title)
        .filter((t): t is string => t != null);
    expect(titles).toContain(renamed);
});

test("delete session removes it from the list", async ({
    baseURL,
    request,
}) => {
    const identity = await bootstrapIdentity(request, baseURL!);
    const agent = await apiCreateAgent(request, baseURL!, identity);
    const session = await apiCreateSession(request, baseURL!, identity, {
        subject_id: agent.id,
    });

    const del = await request.delete(
        `${baseURL}/api/v1/sessions/${session.id}`,
        { headers: authHeaders(identity) },
    );
    expect(del.status()).toBe(204);

    const list = await request.get(`${baseURL}/api/v1/sessions`, {
        headers: authHeaders(identity),
    });
    const ids = ((await list.json()) as Array<{ id: string }>).map((s) => s.id);
    expect(ids).not.toContain(session.id);
});
