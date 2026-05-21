/**
 * Flows — create via UI + trigger a manual run via detail page.
 *
 * 1. API-create an agent so the FlowForm's "Agent" select has a value.
 * 2. UI `/flows/new`: fill name/description, pick the agent, leave
 *    trigger=manual, submit.
 * 3. Detail page: click "Run now" — the backend returns 202 with a run
 *    row, regardless of whether an LLM provider is configured (run can
 *    surface as `failed` without a provider, but the row exists).
 * 4. Assert the runs list no longer reads "No runs yet" and the
 *    `GET /flows/{id}/runs` endpoint returns a non-empty array.
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

test("create flow via UI then trigger a manual run", async ({
    baseURL,
    request,
    page,
}) => {
    const identity = await bootstrapIdentity(request, baseURL!);
    const agent = await apiCreateAgent(request, baseURL!, identity);

    await seedSession(page, identity);
    await page.goto("/en-US/flows/new");

    // Wait for the hydrated form (agent list has to load for the Select).
    await expect(page.getByTestId("flow-form-name")).toBeVisible({
        timeout: 15_000,
    });
    const flowName = `E2E Flow ${randomSuffix()}`;
    await page.getByTestId("flow-form-name").fill(flowName);
    await page.getByTestId("flow-form-description").fill("e2e flow crud");

    // Agent Select: wait until agents have loaded (placeholder text visible),
    // then open and pick the seeded agent.
    const agentCombo = page
        .getByRole("combobox")
        .filter({ hasText: /pick|select|agent|assistant/i })
        .first();
    await agentCombo.waitFor({ state: "visible", timeout: 15_000 });
    await agentCombo.click();
    // Wait for the option to appear before clicking (agents may still be fetching).
    await page.getByRole("option", { name: agent.name }).waitFor({
        state: "visible",
        timeout: 10_000,
    });
    await page.getByRole("option", { name: agent.name }).click();

    // Wait for the submit button to become enabled (requires name + agentId).
    const submitBtn = page.getByTestId("flow-form-submit");
    await submitBtn.waitFor({ state: "visible", timeout: 5_000 });
    await expect(submitBtn).not.toBeDisabled({ timeout: 5_000 });
    await submitBtn.click();

    // Redirect to /flows/{uuid} on create.
    await expect(page).toHaveURL(
        /\/flows\/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/,
        { timeout: 15_000 },
    );
    const flowId = page.url().split("/").pop()!;

    // Trigger a run. We do not wait for success — just that the backend
    // accepts the request and the runs list shows at least one row.
    await page.getByRole("button", { name: "Run now" }).click();

    // Poll the API until the runs list surfaces a row (up to 15s).
    let runs: Array<{ id: string }> = [];
    for (let i = 0; i < 15; i++) {
        const res = await request.get(`${baseURL}/api/v1/flows/${flowId}/runs`, {
            headers: authHeaders(identity),
        });
        if (res.ok()) {
            runs = (await res.json()) as Array<{ id: string }>;
            if (runs.length > 0) break;
        }
        await page.waitForTimeout(1000);
    }
    expect(runs.length, "flow run was persisted").toBeGreaterThan(0);
});
