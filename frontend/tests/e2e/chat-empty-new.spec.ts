/**
 * Chat — empty state at `/chat` + `/chat/new` draft surface shape.
 *
 * `/chat` renders the "No recent sessions" empty card for a cold
 * workspace. `/chat/new?agent=...` is now a **draft** surface (DeepSeek
 * style): the page mounts a header + composer immediately, and only the
 * first sent message triggers `POST /sessions` and the redirect to
 * `/chat/{uuid}`. We assert the draft mount without sending a message
 * to keep this spec snappy and provider-free.
 */
import { expect, test } from "@playwright/test";
import {
    apiCreateAgent,
    bootstrapIdentity,
    requireStack,
    seedSession,
} from "./helpers";

test.beforeEach(async ({ baseURL }) => {
    await requireStack(baseURL);
});

test("/chat shows empty state on a cold workspace", async ({
    baseURL,
    request,
    page,
}) => {
    const identity = await bootstrapIdentity(request, baseURL!);
    await seedSession(page, identity);
    await page.goto("/en-US/chat");

    // Empty-state copy comes from `emptyStates.noSessions`. We match the
    // CTA "New chat" because the empty copy varies with branding.
    // Two "New chat" links may exist (nav bar icon + empty-state button).
    await expect(page.getByRole("link", { name: /New chat/i }).first()).toBeVisible({
        timeout: 15_000,
    });
});

test("/chat/new?agent=... mounts the draft composer without creating a session", async ({
    baseURL,
    request,
    page,
}) => {
    const identity = await bootstrapIdentity(request, baseURL!);
    const agent = await apiCreateAgent(request, baseURL!, identity);
    await seedSession(page, identity);

    await page.goto(`/en-US/chat/new?agent=${agent.id}`);
    // The URL must stay on `/chat/new` until the user actually sends a
    // message — the old auto-create flow leaked an empty session per
    // sidebar click.
    await expect(page).toHaveURL(/\/chat\/new\?agent=/, { timeout: 10_000 });
    // Composer mounts immediately so the user can start typing.
    await expect(page.getByTestId("chat-input")).toBeVisible({
        timeout: 15_000,
    });
});
