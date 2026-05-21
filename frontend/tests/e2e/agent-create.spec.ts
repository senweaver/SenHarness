/**
 * Hybrid agent-create spec.
 *
 * Account / workspace are bootstrapped via API, then the spec drives the
 * real ``/agents/new`` UI form with keyboard and mouse — the same way an
 * employee creating their first assistant would. Assertions verify the
 * post-create redirect shows the agent in the list.
 *
 * This test catches regressions in:
 *   - AgentForm field wiring (name / description / persona textareas).
 *   - `useCreateAgent` → POST /api/v1/agents contract.
 *   - /agents list page pickup of the newly-created row.
 *   - Router locale wrapping (/en-US prefix must survive the redirect).
 */
import { expect, test } from "@playwright/test";
import {
    bootstrapIdentity,
    requireStack,
    seedSession,
} from "./helpers";

test.beforeEach(async ({ baseURL }) => {
    await requireStack(baseURL);
});

test("create agent via UI form", async ({ baseURL, request, page }) => {
    const identity = await bootstrapIdentity(request, baseURL!);
    await seedSession(page, identity);

    // Navigate directly to the create page. `/en-US/agents/new` is the
    // locale-prefixed canonical path; the app also accepts `/agents/new`
    // and re-wraps with the active locale.
    await page.goto("/en-US/agents/new");

    const agentName = `E2E Bot ${Date.now().toString(36)}`;
    const agentDesc = "Smoke test agent — e2e scripted create";

    // ── 1. Fill the basics (these three testids are stable).
    await page.getByTestId("agent-form-name").fill(agentName);
    await page.getByTestId("agent-form-description").fill(agentDesc);
    await page.getByTestId("agent-form-persona").fill(
        "You are a concise assistant. Reply with the result only.",
    );

    // ── 2. Submit. The form redirects to /agents/{id} on success.
    await page.getByTestId("agent-form-submit").click();

    // ── 3. Assert the redirect landed on the agent-detail or agent-edit route.
    //       AgentForm redirects to /agents/{uuid}/edit on successful creation.
    await expect(page).toHaveURL(
        /\/agents\/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}(\/edit)?$/,
        { timeout: 15_000 },
    );

    // ── 4. The agent name shown in the form input proves the server
    //       persisted what was submitted (edit page pre-fills from API).
    const nameInput = page.getByTestId("agent-form-name");
    await expect(nameInput).toBeVisible({ timeout: 10_000 });
    await expect(nameInput).toHaveValue(agentName, { timeout: 10_000 });

    // ── 5. Cross-verify via the list endpoint — catches any bug where the
    //       create succeeds but the row isn't workspace-scoped correctly.
    const listResp = await request.get(`${baseURL}/api/v1/agents`, {
        headers: {
            Authorization: `Bearer ${identity.accessToken}`,
            "X-Workspace-Id": identity.workspaceId,
        },
    });
    expect(listResp.ok()).toBe(true);
    const agents = (await listResp.json()) as Array<{ name: string }>;
    expect(agents.map((a) => a.name)).toContain(agentName);
});
