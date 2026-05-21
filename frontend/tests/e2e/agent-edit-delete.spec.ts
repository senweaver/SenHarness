/**
 * Agents — edit + delete round-trip via UI.
 *
 * 1. API-create an agent.
 * 2. Drive `/agents/{id}/edit`: rename → Save → URL remains on edit
 *    (AgentForm uses inline save) → detail page picks up the new name.
 * 3. Drive detail-page delete: accept the `confirm()` dialog → assert
 *    we bounce to `/agents` and the agent is gone from the API list.
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

test("edit agent via UI persists the new name", async ({
    baseURL,
    request,
    page,
}) => {
    const identity = await bootstrapIdentity(request, baseURL!);
    const agent = await apiCreateAgent(request, baseURL!, identity);
    const renamed = `Renamed ${randomSuffix()}`;

    await seedSession(page, identity);
    await page.goto(`/en-US/agents/${agent.id}/edit`, {
        waitUntil: "domcontentloaded",
    });

    // Wait for the form to hydrate with the existing value before typing.
    const nameField = page.getByTestId("agent-form-name");
    await expect(nameField).toHaveValue(agent.name, { timeout: 15_000 });
    await nameField.fill(renamed);
    await page.getByTestId("agent-form-submit").click();

    // Save leaves us on the edit page — navigate to detail to assert
    // persistence (cheaper than polling the toast).
    await page.goto(`/en-US/agents/${agent.id}`);
    await expect(
        page.getByRole("heading", { level: 1, name: renamed }),
    ).toBeVisible({ timeout: 15_000 });

    // Cross-verify via API.
    const api = await request.get(`${baseURL}/api/v1/agents/${agent.id}`, {
        headers: authHeaders(identity),
    });
    expect(api.ok()).toBe(true);
    expect(((await api.json()) as { name: string }).name).toBe(renamed);
});

test("delete agent via UI removes it from the list", async ({
    baseURL,
    request,
    page,
}) => {
    const identity = await bootstrapIdentity(request, baseURL!);
    const agent = await apiCreateAgent(request, baseURL!, identity);

    await seedSession(page, identity);
    // AgentForm hosts the destructive delete button, reachable via the
    // edit page.
    await page.goto(`/en-US/agents/${agent.id}/edit`);
    await expect(page.getByTestId("agent-form-name")).toHaveValue(agent.name, {
        timeout: 15_000,
    });

    // Auto-accept the `confirm()` dialog that asks for deletion consent.
    page.once("dialog", (d) => d.accept());
    await page.getByRole("button", { name: /^Delete$/ }).click();

    // After delete we land on `/agents`.
    await expect(page).toHaveURL(/\/agents\/?$/, { timeout: 15_000 });

    const list = await request.get(`${baseURL}/api/v1/agents`, {
        headers: authHeaders(identity),
    });
    const names = ((await list.json()) as Array<{ name: string }>).map(
        (a) => a.name,
    );
    expect(names).not.toContain(agent.name);
});
