/**
 * Settings → Channels (IM integrations) — CRUD + token rotation round-trip.
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

test("channel CRUD + token rotation", async ({ baseURL, request, page }) => {
    const owner = await bootstrapIdentity(request, baseURL!);
    // Channel creation requires a default_agent_id or default_squad_id.
    const agent = await apiCreateAgent(request, baseURL!, owner);
    await seedSession(page, owner);
    await page.goto("/en-US/channels");
    await expect(
        page.getByRole("heading", { level: 1, name: /IM channels/ }),
    ).toBeVisible({ timeout: 15_000 });

    const name = `E2E Channel ${randomSuffix()}`;
    const create = await request.post(`${baseURL}/api/v1/channels`, {
        headers: authHeaders(owner),
        data: {
            name,
            kind: "webhook",
            default_agent_id: agent.id,
            config_json: {},
            enabled: true,
            metadata_json: {},
        },
    });
    expect(create.status(), `create status ${create.status()}`).toBe(201);
    const channel = (await create.json()) as { id: string };

    // Rotate token — response shape includes the new one-time token.
    const rotate = await request.post(
        `${baseURL}/api/v1/channels/${channel.id}/rotate-token`,
        { headers: authHeaders(owner) },
    );
    expect(rotate.ok()).toBe(true);

    // Delete.
    const del = await request.delete(
        `${baseURL}/api/v1/channels/${channel.id}`,
        { headers: authHeaders(owner) },
    );
    expect(del.status()).toBe(204);

    // Not in list.
    const list = await request.get(`${baseURL}/api/v1/channels`, {
        headers: authHeaders(owner),
    });
    const names = ((await list.json()) as Array<{ name: string }>).map(
        (c) => c.name,
    );
    expect(names).not.toContain(name);
});
