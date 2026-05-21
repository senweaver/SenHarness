/**
 * Chat history — existing messages render when navigating into a
 * pre-populated session.
 *
 * The chat detail page pulls history via `GET /sessions/{id}/messages`
 * on mount (the WebSocket only carries new turns). We seed a user +
 * assistant message via the REST append-message endpoint and assert
 * both show up in the transcript.
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

test("pre-seeded messages render in /chat/[sessionId]", async ({
    baseURL,
    request,
    page,
}) => {
    const identity = await bootstrapIdentity(request, baseURL!);
    const agent = await apiCreateAgent(request, baseURL!, identity);
    const session = await apiCreateSession(request, baseURL!, identity, {
        subject_id: agent.id,
    });

    const userText = `hello from e2e ${randomSuffix()}`;
    const assistantText = `welcome reply ${randomSuffix()}`;

    const userMsg = await request.post(
        `${baseURL}/api/v1/sessions/${session.id}/messages`,
        {
            headers: authHeaders(identity),
            data: {
                role: "user",
                content_json: { text: userText },
                attachments_json: [],
            },
        },
    );
    expect(userMsg.status(), "seed user msg").toBe(201);

    const asstMsg = await request.post(
        `${baseURL}/api/v1/sessions/${session.id}/messages`,
        {
            headers: authHeaders(identity),
            data: {
                role: "assistant",
                content_json: { text: assistantText },
                attachments_json: [],
            },
        },
    );
    expect(asstMsg.status(), "seed assistant msg").toBe(201);

    await seedSession(page, identity);
    await page.goto(`/en-US/chat/${session.id}`);

    // Wait for the transcript area (chat-input appears after mount).
    await expect(page.getByTestId("chat-input")).toBeVisible({
        timeout: 15_000,
    });
    await expect(page.getByText(userText)).toBeVisible({ timeout: 10_000 });
    await expect(page.getByText(assistantText)).toBeVisible({ timeout: 10_000 });
});
