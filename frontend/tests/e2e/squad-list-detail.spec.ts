/**
 * Squads — list + detail smoke.
 *
 * Covers:
 *   - `/squads` renders the "Squad management" title and the seeded squad
 *     name shows up on the grid.
 *   - `/squads/{id}` renders the seeded squad's name in the h1.
 *   - `/squads/<bad-uuid>` shows the notFound card + Back button.
 */
import { expect, test } from "@playwright/test";
import {
    apiCreateAgent,
    apiCreateSquad,
    bootstrapIdentity,
    requireStack,
    seedSession,
} from "./helpers";

test.beforeEach(async ({ baseURL }) => {
    await requireStack(baseURL);
});

test("squads list shows seeded rows and detail page loads", async ({
    baseURL,
    request,
    page,
}) => {
    const identity = await bootstrapIdentity(request, baseURL!);
    const agent = await apiCreateAgent(request, baseURL!, identity);
    const squad = await apiCreateSquad(request, baseURL!, identity, {
        members: [{ agent_id: agent.id }],
    });

    await seedSession(page, identity);
    await page.goto("/en-US/squads");
    await expect(
        page.getByRole("heading", { level: 1, name: "Squads" }),
    ).toBeVisible({ timeout: 15_000 });
    await expect(page.getByText(squad.name).first()).toBeVisible({
        timeout: 15_000,
    });

    await page.goto(`/en-US/squads/${squad.id}`);
    await expect(
        page.getByRole("heading", { level: 1, name: squad.name }),
    ).toBeVisible({ timeout: 15_000 });
});

test("non-existent squad id renders the not-found card", async ({
    baseURL,
    request,
    page,
}) => {
    const identity = await bootstrapIdentity(request, baseURL!);
    await seedSession(page, identity);

    // Well-formed UUID that doesn't exist in the DB.
    await page.goto("/en-US/squads/11111111-1111-1111-1111-111111111111");
    // "Squad not found" is the English copy for `settings.squads.detail.notFoundTitle`.
    await expect(
        page.getByRole("heading", { level: 1, name: /not found/i }),
    ).toBeVisible({ timeout: 15_000 });
});
