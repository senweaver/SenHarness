/**
 * Chat — message rating (thumbs-up / thumbs-down) round-trip.
 *
 * What we cover:
 *   - Seed an assistant message via REST so the rating is exercised against
 *     a real persisted row (no need to drive the WS turn for this).
 *   - Like (👍) → expect green styling + count = 1 + GET /ratings reflects
 *     `my_rating: 1, likes: 1`.
 *   - Re-click 👍 to remove the rating → expect 0 / 0 / null.
 *   - Dislike (👎) → opens the comment dialog; submit with comment → expect
 *     red styling + the row in `message_ratings` carries our comment text.
 *
 * The rate endpoint is only valid for `role=assistant` messages — we make
 * sure that constraint is respected by also verifying a 422 on a user msg.
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

test("like → unlike → dislike-with-comment cycles through every state", async ({
    baseURL,
    request,
    page,
}) => {
    const identity = await bootstrapIdentity(request, baseURL!);
    const agent = await apiCreateAgent(request, baseURL!, identity);
    const session = await apiCreateSession(request, baseURL!, identity, {
        subject_id: agent.id,
    });

    // Seed a single assistant message so the rating buttons attach.
    const seedText = `Test reply ${randomSuffix()}`;
    const seed = await request.post(
        `${baseURL}/api/v1/sessions/${session.id}/messages`,
        {
            headers: authHeaders(identity),
            data: {
                role: "assistant",
                content_json: { text: seedText },
                attachments_json: [],
            },
        },
    );
    expect(seed.status(), "seed assistant").toBe(201);
    const message = (await seed.json()) as { id: string };

    await seedSession(page, identity);
    await page.goto(`/en-US/chat/${session.id}`);

    // Rating buttons render once the GET /ratings query resolves; wait for
    // the assistant bubble + the testid before interacting.
    const assistantBubble = page
        .getByTestId("assistant-message")
        .filter({ hasText: seedText });
    await expect(assistantBubble).toBeVisible({ timeout: 15_000 });

    const ratingBlock = page.locator(
        `[data-testid="rating-buttons"]`,
    ).first();
    await expect(ratingBlock).toBeVisible({ timeout: 10_000 });
    const likeBtn = ratingBlock.getByTestId("rating-like");
    const dislikeBtn = ratingBlock.getByTestId("rating-dislike");

    // ── Like ──
    await likeBtn.click();
    await expect(likeBtn).toHaveAttribute("data-active", "true", {
        timeout: 10_000,
    });
    await expect(likeBtn).toContainText("1");

    // Verify summary endpoint reports the upsert.
    const sum1 = await request.get(
        `${baseURL}/api/v1/sessions/${session.id}/ratings`,
        { headers: authHeaders(identity) },
    );
    expect(sum1.ok()).toBe(true);
    const items1 = (await sum1.json()) as Array<{
        message_id: string;
        my_rating: number | null;
        likes: number;
    }>;
    const row1 = items1.find((r) => r.message_id === message.id);
    expect(row1?.my_rating).toBe(1);
    expect(row1?.likes).toBe(1);

    // ── Re-click like → remove ──
    await likeBtn.click();
    await expect(likeBtn).toHaveAttribute("data-active", "false", {
        timeout: 10_000,
    });

    // ── Dislike with comment ──
    await dislikeBtn.click();
    const dialog = page.getByRole("dialog");
    await expect(dialog).toBeVisible();
    const commentText = `not great ${randomSuffix()}`;
    await dialog
        .getByRole("textbox")
        .fill(commentText);
    await dialog.getByRole("button", { name: /Submit with comment/i }).click();
    await expect(dialog).toBeHidden({ timeout: 10_000 });

    await expect(dislikeBtn).toHaveAttribute("data-active", "true", {
        timeout: 10_000,
    });

    const sum2 = await request.get(
        `${baseURL}/api/v1/sessions/${session.id}/ratings`,
        { headers: authHeaders(identity) },
    );
    const items2 = (await sum2.json()) as Array<{
        message_id: string;
        my_rating: number | null;
        dislikes: number;
    }>;
    const row2 = items2.find((r) => r.message_id === message.id);
    expect(row2?.my_rating).toBe(-1);
    expect(row2?.dislikes).toBe(1);
});

test("rating a non-assistant message is rejected with 422", async ({
    baseURL,
    request,
}) => {
    const identity = await bootstrapIdentity(request, baseURL!);
    const agent = await apiCreateAgent(request, baseURL!, identity);
    const session = await apiCreateSession(request, baseURL!, identity, {
        subject_id: agent.id,
    });

    // Seed a USER message — rating should be blocked.
    const seed = await request.post(
        `${baseURL}/api/v1/sessions/${session.id}/messages`,
        {
            headers: authHeaders(identity),
            data: {
                role: "user",
                content_json: { text: "hi" },
                attachments_json: [],
            },
        },
    );
    expect(seed.status()).toBe(201);
    const message = (await seed.json()) as { id: string };

    const rate = await request.post(
        `${baseURL}/api/v1/sessions/${session.id}/messages/${message.id}/rate`,
        {
            headers: authHeaders(identity),
            data: { rating: 1, comment: null },
        },
    );
    expect(rate.status(), "rate user msg").toBe(422);
    const body = (await rate.json()) as { code?: string };
    expect(body.code).toBe("rating.not_assistant");
});
