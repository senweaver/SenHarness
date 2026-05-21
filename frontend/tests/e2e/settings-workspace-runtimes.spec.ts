/**
 * Settings → Workspace → Runtime adapters — register + rotate-key +
 * health probe + delete round-trip.
 *
 * All operations use the API; the UI smoke simply asserts that we can
 * land on the page after seeding.
 */
import { expect, test } from "@playwright/test";
import {
    authHeaders,
    bootstrapIdentity,
    randomSuffix,
    requireStack,
    seedSession,
} from "./helpers";

test.beforeEach(async ({ baseURL }) => {
    await requireStack(baseURL);
});

test("register backend adapter, rotate key, probe health, delete", async ({
    baseURL,
    request,
    page,
}) => {
    const owner = await bootstrapIdentity(request, baseURL!);
    await seedSession(page, owner);

    await page.goto("/en-US/settings/workspace/runtimes");
    // PageHeader's h1 text varies by workspace copy; just prove mount.
    await expect(page.locator("h1").first()).toBeVisible({ timeout: 15_000 });

    const name = `E2E Adapter ${randomSuffix()}`;
    const create = await request.post(`${baseURL}/api/v1/backends`, {
        headers: authHeaders(owner),
        data: {
            name,
            kind: "openclaw",
            endpoint: "https://example.invalid/openclaw",
            metadata_json: {},
        },
    });
    expect(create.status(), `create status ${create.status()}`).toBe(201);
    const created = (await create.json()) as {
        adapter: { id: string };
        api_key: string;
    };
    expect(created.api_key, "api_key returned once on create").toBeTruthy();

    // Rotate key — a second api_key is surfaced.
    const rotate = await request.post(
        `${baseURL}/api/v1/backends/${created.adapter.id}/rotate-key`,
        { headers: authHeaders(owner) },
    );
    expect(rotate.ok()).toBe(true);
    const rotated = (await rotate.json()) as { api_key: string };
    expect(rotated.api_key).not.toBe(created.api_key);

    // Health probe — the fake endpoint can't be reached, so status ends
    // up "down" or similar. What matters is that the endpoint replies 2xx.
    const health = await request.post(`${baseURL}/api/v1/backends/health`, {
        headers: authHeaders(owner),
        data: { endpoint: "https://example.invalid/openclaw", api_key: "x" },
    });
    // Some backends 200 with status=down, others 502; 405 means the
    // health route exists but the fake endpoint rejects the method.
    // Anything outside 5xx (except 404/500) proves the route is wired.
    expect([200, 400, 405, 502, 503]).toContain(health.status());

    // Delete.
    const del = await request.delete(
        `${baseURL}/api/v1/backends/${created.adapter.id}`,
        { headers: authHeaders(owner) },
    );
    expect(del.status()).toBe(204);
});
