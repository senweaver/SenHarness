/**
 * Moderation — workspace moderator (owner) decides a report.
 *
 * Scenario:
 *   1. Publisher bootstraps a workspace and publishes a public agent.
 *   2. Reporter (second identity) files a spam report.
 *   3. Publisher (who is workspace owner, i.e. an admin/moderator) opens
 *      `/settings/moderation`, switches the filter to "pending", and
 *      decides via API — status flips to `reviewed`.
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

test("workspace owner can decide a pending report", async ({
    baseURL,
    request,
    page,
}) => {
    const publisher = await bootstrapIdentity(request, baseURL!);
    const agent = await apiCreateAgent(request, baseURL!, publisher, {
        visibility: "public",
    });

    const reporter = await bootstrapIdentity(request, baseURL!);
    const report = await request.post(
        `${baseURL}/api/v1/agents/${agent.id}/report`,
        {
            headers: authHeaders(reporter),
            data: { reason: "spam", description: "e2e decide flow" },
        },
    );
    expect(report.ok(), `report status ${report.status()}`).toBe(true);
    const { id: reportId } = (await report.json()) as { id: string };

    // The publisher's moderation list shows it as pending.
    const pending = await request.get(
        `${baseURL}/api/v1/moderation/reports?status=pending`,
        { headers: authHeaders(publisher) },
    );
    const pendingIds = ((await pending.json()) as Array<{ id: string }>).map(
        (r) => r.id,
    );
    expect(pendingIds, "pending list contains new report").toContain(reportId);

    // Decide via API (the UI sends the same PATCH).
    const decide = await request.patch(
        `${baseURL}/api/v1/moderation/reports/${reportId}`,
        {
            headers: authHeaders(publisher),
            data: { decision: "dismissed", note: "false positive" },
        },
    );
    expect(decide.ok(), `decide status ${decide.status()}`).toBe(true);
    expect(((await decide.json()) as { status: string }).status).toBe(
        "dismissed",
    );

    // Moderation page mounts and renders without 5xx.
    await seedSession(page, publisher);
    await page.goto("/en-US/settings/moderation");
    await expect(
        page.getByRole("heading", { level: 1, name: "Marketplace moderation" }),
    ).toBeVisible({ timeout: 15_000 });
});
