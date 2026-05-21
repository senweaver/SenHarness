/**
 * Workspace MEMORY.md editor — owner writes, API persists.
 *
 * `bootstrapIdentity()` makes the fresh user a workspace owner, so the
 * admin-only gate on this page passes. We:
 *   1. Mount the page and wait for `workspace-memory-page`.
 *   2. Type into the `workspace-memory-textarea`.
 *   3. Click `workspace-memory-save`.
 *   4. Verify `GET /api/v1/memory-profiles/workspace` returns the new text.
 */
import { expect, test } from "@playwright/test";
import {
    authHeaders,
    bootstrapIdentity,
    randomSuffix,
    requireStack,
    seedSession,
} from "./helpers";

test.beforeEach(async ({ baseURL }) => {
    await requireStack(baseURL);
});

test("owner edits workspace MEMORY.md and API persists it", async ({
    baseURL,
    request,
    page,
}) => {
    const owner = await bootstrapIdentity(request, baseURL!);
    await seedSession(page, owner);

    await page.goto("/en-US/settings/workspace/memory", {
        waitUntil: "domcontentloaded",
    });
    await expect(page.getByTestId("workspace-memory-page")).toBeVisible({
        timeout: 30_000,
    });

    const body = `# Workspace defaults\n\n- e2e marker: ${randomSuffix()}\n`;
    await page.getByTestId("workspace-memory-textarea").fill(body);
    await page.getByTestId("workspace-memory-save").click();
    // Sonner toast surfaces on success; wait for a moment for the mutation
    // + invalidation to flush.
    await expect(page.getByText(/Saved/i).first()).toBeVisible({
        timeout: 10_000,
    });

    const resp = await request.get(
        `${baseURL}/api/v1/memory-profiles/workspace`,
        { headers: authHeaders(owner) },
    );
    expect(resp.ok()).toBe(true);
    const got = (await resp.json()) as { content_md: string | null };
    // Some backends normalise trailing whitespace; compare trimmed.
    expect(got.content_md?.trimEnd()).toBe(body.trimEnd());
});
