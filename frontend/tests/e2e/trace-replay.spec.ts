/**
 * Trace replay page smoke — mount + heading + graceful empty state.
 *
 * Cold sessions (no tool calls, no LLM replies) will render the page
 * with an empty event list. We assert the h1 and description land, not
 * that the list is non-empty, so the spec stays useful even without an
 * LLM provider configured.
 */
import { expect, test } from "@playwright/test";
import {
    apiCreateAgent,
    apiCreateSession,
    bootstrapIdentity,
    requireStack,
    seedSession,
} from "./helpers";

test.beforeEach(async ({ baseURL }) => {
    await requireStack(baseURL);
});

test("trace replay page mounts for a freshly-created session", async ({
    baseURL,
    request,
    page,
}) => {
    const identity = await bootstrapIdentity(request, baseURL!);
    const agent = await apiCreateAgent(request, baseURL!, identity);
    const session = await apiCreateSession(request, baseURL!, identity, {
        subject_id: agent.id,
    });

    await seedSession(page, identity);
    await page.goto(`/en-US/traces/${session.id}`);

    await expect(
        page.getByRole("heading", { level: 1, name: "Trace replay" }),
    ).toBeVisible({ timeout: 20_000 });
});
