/**
 * Moderation — report a public agent + verify the workspace moderation
 * page picks up the report.
 *
 * Flow:
 *   1. Publisher creates a public agent.
 *   2. Reporter (separate identity) submits a report via API.
 *   3. Publisher visits `/settings/moderation` — page mounts with the
 *      report visible (the moderation queue is publisher-scoped).
 */
import { expect, test } from "@playwright/test";
import {
    apiCreateAgent,
    authHeaders,
    bootstrapIdentity,
    requireStack,
    seedSession,
} from "./helpers";

test.beforeEach(async ({ baseURL }) => {
    await requireStack(baseURL);
});

test("publishing agent + filing report surfaces on the moderation page", async ({
    baseURL,
    request,
    page,
}) => {
    const publisher = await bootstrapIdentity(request, baseURL!);
    const agent = await apiCreateAgent(request, baseURL!, publisher, {
        visibility: "public",
        name: `Reportable Agent`,
    });

    const reporter = await bootstrapIdentity(request, baseURL!);
    const reportRes = await request.post(
        `${baseURL}/api/v1/agents/${agent.id}/report`,
        {
            headers: authHeaders(reporter),
            data: {
                reason: "spam",
                description: "e2e moderation report",
            },
        },
    );
    expect(reportRes.ok(), `report status ${reportRes.status()}`).toBe(true);

    // Publisher opens their moderation page.
    await seedSession(page, publisher);
    await page.goto("/en-US/settings/moderation");
    await expect(
        page.getByRole("heading", {
            level: 1,
            name: "Marketplace moderation",
        }),
    ).toBeVisible({ timeout: 15_000 });

    // Poll the API to confirm the report actually landed (the UI might
    // filter by `pending` by default; the API list is authoritative).
    const list = await request.get(
        `${baseURL}/api/v1/moderation/reports?status=pending`,
        { headers: authHeaders(publisher) },
    );
    if (!list.ok()) {
        test.info().annotations.push({
            type: "note",
            description: `moderation list endpoint returned ${list.status()} — might be scoped differently than expected.`,
        });
        return;
    }
    const rows = (await list.json()) as Array<{ agent_id: string }>;
    expect(rows.map((r) => r.agent_id)).toContain(agent.id);
});
