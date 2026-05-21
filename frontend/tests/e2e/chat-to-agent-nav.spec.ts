/**
 * Chat → Agent direct-jump affordances.
 *
 * The plan added two new ways for a workspace member to land on the agent
 * detail / edit page from inside a chat session, replacing the previous
 * 11-px hidden ``IconExternalLink`` in the popover:
 *
 *   1. ``ChatHeader`` left-of-title agent-avatar button → ``/agents/{id}``.
 *   2. ``SessionHeaderPopover`` "Open agent" + "Edit settings" buttons —
 *      both visible to all workspace members so the UI matches the backend's
 *      ``ensure_member_access`` policy on ``PATCH /v1/agents/{id}``.
 *
 * This spec drives the live UI on a freshly-created p2p session.
 */
import { expect, test } from "@playwright/test";

import { requireStack, seedSession } from "./helpers";
import { bootstrapPersonalIdentity, createAgent, createSession } from "./_bootstrap";

test.beforeEach(async ({ baseURL }) => {
    await requireStack(baseURL);
});

test("chat header avatar single-click jumps to agent detail", async ({
    baseURL,
    request,
    page,
}) => {
    const identity = await bootstrapPersonalIdentity(request, baseURL!);
    const agent = await createAgent(request, baseURL!, identity);
    const session = await createSession(request, baseURL!, identity, agent.id);

    await seedSession(page, identity);
    await page.goto(`/en-US/chat/${session.id}`, {
        waitUntil: "domcontentloaded",
    });

    const avatar = page.getByTestId("chat-header-agent-avatar");
    await expect(avatar).toBeVisible({ timeout: 15_000 });

    await avatar.click();
    await expect(page).toHaveURL(
        new RegExp(`/agents/${agent.id}(?:[/?#]|$)`),
        { timeout: 15_000 },
    );
});

test("session title popover exposes Open + Edit buttons", async ({
    baseURL,
    request,
    page,
}) => {
    const identity = await bootstrapPersonalIdentity(request, baseURL!);
    const agent = await createAgent(request, baseURL!, identity);
    const session = await createSession(request, baseURL!, identity, agent.id);

    await seedSession(page, identity);
    await page.goto(`/en-US/chat/${session.id}`, {
        waitUntil: "domcontentloaded",
    });

    await page.getByTestId("chat-header-title-trigger").click();

    const openBtn = page.getByRole("link", { name: /Open agent/i });
    const editBtn = page.getByRole("link", { name: /Edit settings/i });
    await expect(openBtn).toBeVisible({ timeout: 5_000 });
    await expect(editBtn).toBeVisible();

    await editBtn.click();
    await expect(page).toHaveURL(
        new RegExp(`/agents/${agent.id}/edit(?:[/?#]|$)`),
        { timeout: 15_000 },
    );
});
