/**
 * Settings → Secrets — create + reveal + delete via API, with a UI mount
 * check for the settings page.
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

test("secret CRUD + reveal", async ({ baseURL, request, page }) => {
    const owner = await bootstrapIdentity(request, baseURL!);
    await seedSession(page, owner);
    await page.goto("/en-US/settings/secrets");
    await expect(
        page.getByRole("heading", { level: 1, name: /Secrets/ }),
    ).toBeVisible({ timeout: 15_000 });

    const name = `E2E_SECRET_${randomSuffix().toUpperCase()}`;
    const value = `val-${randomSuffix()}`;
    const create = await request.post(`${baseURL}/api/v1/secrets`, {
        headers: authHeaders(owner),
        data: {
            name,
            value,
            kind: "generic",
            metadata_json: {},
            required_approval: false,
        },
    });
    expect(create.status(), `create status ${create.status()}`).toBe(201);
    const secret = (await create.json()) as { id: string };

    // Reveal round-trips the plaintext value.
    const reveal = await request.post(
        `${baseURL}/api/v1/secrets/${secret.id}/reveal`,
        { headers: authHeaders(owner) },
    );
    expect(reveal.ok()).toBe(true);
    const revealed = (await reveal.json()) as { value: string };
    expect(revealed.value).toBe(value);

    // Delete.
    const del = await request.delete(`${baseURL}/api/v1/secrets/${secret.id}`, {
        headers: authHeaders(owner),
    });
    expect(del.status()).toBe(204);

    // Not in list.
    const list = await request.get(`${baseURL}/api/v1/secrets`, {
        headers: authHeaders(owner),
    });
    const names = ((await list.json()) as Array<{ name: string }>).map(
        (s) => s.name,
    );
    expect(names).not.toContain(name);
});
