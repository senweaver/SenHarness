/**
 * Home page (HeroPrompt) spec.
 *
 * Covers the "logged-in landing → type → send → chat session created"
 * path. This is a high-signal smoke because three major systems have to
 * cooperate: auth store hydrate, `useCreateSession` mutation, and the
 * post-create navigation to `/chat/[id]`.
 *
 * We bootstrap an agent first so `useRecentAgents` picks it up as the
 * default subject; without a subject the send button is disabled.
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

test("home page welcomes the user and sending a prompt opens a session", async ({
    baseURL,
    request,
    page,
}) => {
    const identity = await bootstrapIdentity(request, baseURL!);
    // Pre-create an agent so the HeroPrompt can auto-pick it as subject.
    await apiCreateAgent(request, baseURL!, identity);

    await seedSession(page, identity);
    await page.goto("/en-US");

    // Welcome h1 — workspace branding overrides the translation, so the
    // heading text varies by locale/branding. Just verify the h1 is mounted.
    await expect(page.locator("h1").first()).toBeVisible({ timeout: 20_000 });

    // Wait for the agent pill (subject picker) to show an agent name —
    // useRecentAgents auto-selects the first available agent, which we
    // seeded above. Without this wait the session create lands with
    // subject_id: null.
    const agentPill = page.locator("section span").filter({ hasText: /E2E Agent/ });
    await agentPill.waitFor({ state: "visible", timeout: 15_000 });

    // The composer is a Textarea inside the HeroPrompt section. It has
    // no testid, but the placeholder ("Describe a task, paste context…")
    // is stable enough to target without binding to exact copy.
    const composer = page.locator("textarea").first();
    await composer.waitFor({ state: "visible", timeout: 10_000 });
    await composer.fill("Hello from the home page e2e test.");

    await page.getByRole("button", { name: "send" }).click();

    // On success we navigate to `/en-US/chat/{uuid}`.
    await expect(page).toHaveURL(/\/chat\/[0-9a-f-]{36}/, { timeout: 20_000 });
});
