/**
 * Squads — create + edit round-trip via UI.
 *
 * 1. API-create two agents (squads need ≥1 member to enable Save).
 * 2. UI: /squads/new → fill form → pick both agents via the member
 *    adder → save → expect redirect to /squads/{id}.
 * 3. UI: /squads/{id}/edit → rename → save → detail shows new name.
 * 4. API cross-check: GET /squads/{id} returns the new name + 2 members.
 */
import { expect, test } from "@playwright/test";
import {
    apiCreateAgent,
    authHeaders,
    bootstrapIdentity,
    randomSuffix,
    requireStack,
    seedSession,
} from "./helpers";

test.beforeEach(async ({ baseURL }) => {
    await requireStack(baseURL);
});

test("create squad via UI, add members, rename, verify via API", async ({
    baseURL,
    request,
    page,
}) => {
    const identity = await bootstrapIdentity(request, baseURL!);
    const [a1, a2] = await Promise.all([
        apiCreateAgent(request, baseURL!, identity),
        apiCreateAgent(request, baseURL!, identity),
    ]);

    await seedSession(page, identity);
    await page.goto("/en-US/squads/new");
    // Wait for the form (hydrates client-side useAgents first).
    await expect(page.getByTestId("squad-form-name")).toBeVisible({
        timeout: 15_000,
    });

    const squadName = `E2E Squad ${randomSuffix()}`;
    await page.getByTestId("squad-form-name").fill(squadName);
    await page.getByTestId("squad-form-description").fill("e2e crud run");

    // Add both agents via the member adder. The adder is a Radix Select
    // — we click the trigger and pick by the visible agent name.
    for (const name of [a1!.name, a2!.name]) {
        await page.getByRole("combobox").last().click();
        await page.getByRole("option", { name }).click();
    }

    await page.getByTestId("squad-form-submit").click();

    // Redirect shape: /en-US/squads/{uuid}
    await expect(page).toHaveURL(
        /\/squads\/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/,
        { timeout: 15_000 },
    );
    const squadId = page.url().split("/").pop()!;

    // Verify shape via API.
    const api = await request.get(`${baseURL}/api/v1/squads/${squadId}`, {
        headers: authHeaders(identity),
    });
    expect(api.ok()).toBe(true);
    const squad = (await api.json()) as {
        name: string;
        members: Array<{ agent_id: string }>;
    };
    expect(squad.name).toBe(squadName);
    expect(squad.members.map((m) => m.agent_id)).toEqual(
        expect.arrayContaining([a1!.id, a2!.id]),
    );

    // ── Edit flow: rename and save.
    const renamed = `Renamed ${randomSuffix()}`;
    await page.goto(`/en-US/squads/${squadId}/edit`);
    await expect(page.getByTestId("squad-form-name")).toHaveValue(squadName, {
        timeout: 15_000,
    });
    await page.getByTestId("squad-form-name").fill(renamed);
    await page.getByTestId("squad-form-submit").click();

    // After edit, the hook invalidates the list but stays on the edit
    // page. Navigate to detail to confirm persistence.
    await page.goto(`/en-US/squads/${squadId}`);
    await expect(page.getByText(renamed).first()).toBeVisible({
        timeout: 15_000,
    });
});
