/**
 * Flows list smoke — mount + seeded row visible.
 */
import { expect, test } from "@playwright/test";
import {
    apiCreateAgent,
    apiCreateFlow,
    bootstrapIdentity,
    requireStack,
    seedSession,
} from "./helpers";

test.beforeEach(async ({ baseURL }) => {
    await requireStack(baseURL);
});

test("flows list page mounts and shows seeded flow", async ({
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
    await page.goto("/en-US/flows");

    await expect(
        page.getByRole("heading", { level: 1, name: "Flows" }),
    ).toBeVisible({ timeout: 15_000 });
    await expect(page.getByText(flow.name).first()).toBeVisible({
        timeout: 15_000,
    });
});
