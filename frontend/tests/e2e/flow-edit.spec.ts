/**
 * Flows — edit existing flow via /flows/[id]/edit.
 *
 * Drives the **classic** editor mode (FlowForm) — the visual canvas uses
 * drag-and-drop that's brittle under e2e; canvas-level tests should live
 * as component tests instead.
 */
import { expect, test } from "@playwright/test";
import {
    apiCreateAgent,
    apiCreateFlow,
    authHeaders,
    bootstrapIdentity,
    randomSuffix,
    requireStack,
    seedSession,
} from "./helpers";

test.beforeEach(async ({ baseURL }) => {
    await requireStack(baseURL);
});

test("edit flow via /flows/[id]/edit classic form", async ({
    baseURL,
    request,
    page,
}) => {
    const identity = await bootstrapIdentity(request, baseURL!);
    const agent = await apiCreateAgent(request, baseURL!, identity);
    const flow = await apiCreateFlow(request, baseURL!, identity, {
        agent_id: agent.id,
    });

    await seedSession(page, identity);
    await page.goto(`/en-US/flows/${flow.id}/edit`);

    // Classic mode is default when graph_json is empty — our API factory
    // creates a manual flow without a graph. Wait for the form to hydrate
    // with the existing name before typing.
    await expect(page.getByTestId("flow-form-name")).toHaveValue(flow.name, {
        timeout: 15_000,
    });

    const renamed = `Renamed Flow ${randomSuffix()}`;
    await page.getByTestId("flow-form-name").fill(renamed);
    await page.getByTestId("flow-form-submit").click();

    // The form stays on the edit page after save; toast + API confirm
    // persistence.
    const api = await request.get(`${baseURL}/api/v1/flows/${flow.id}`, {
        headers: authHeaders(identity),
    });
    // Allow a brief settle for the mutation.
    for (let i = 0; i < 10; i++) {
        const body = (await api.json()) as { name: string };
        if (body.name === renamed) break;
        await page.waitForTimeout(500);
    }
    const final = await request.get(`${baseURL}/api/v1/flows/${flow.id}`, {
        headers: authHeaders(identity),
    });
    expect(((await final.json()) as { name: string }).name).toBe(renamed);
});
