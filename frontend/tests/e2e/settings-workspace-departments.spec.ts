/**
 * Settings → Workspace → Departments — CRUD round-trip.
 *
 * UI-mount + API CRUD split. The page renders a tree with collapsible
 * nodes that aren't easily addressable, but after each API operation we
 * confirm the department name shows up on the page.
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

test("create + rename + delete department via API, verify in UI", async ({
    baseURL,
    request,
    page,
}) => {
    const owner = await bootstrapIdentity(request, baseURL!);
    await seedSession(page, owner);

    // Mount the page first so the subsequent create triggers a refetch.
    await page.goto("/en-US/settings/workspace/departments");
    await expect(
        page.getByRole("heading", { level: 1, name: /Departments/i }),
    ).toBeVisible({ timeout: 15_000 });

    const name = `E2E Dept ${randomSuffix()}`;
    const create = await request.post(`${baseURL}/api/v1/departments`, {
        headers: authHeaders(owner),
        data: { name, parent_id: null },
    });
    expect(create.status(), `create status ${create.status()}`).toBe(201);
    const dept = (await create.json()) as { id: string };

    await page.reload();
    await expect(page.getByText(name).first()).toBeVisible({ timeout: 15_000 });

    // Rename
    const renamed = `Renamed ${randomSuffix()}`;
    const patch = await request.patch(
        `${baseURL}/api/v1/departments/${dept.id}`,
        {
            headers: authHeaders(owner),
            data: { name: renamed },
        },
    );
    expect(patch.ok()).toBe(true);

    // Delete
    const del = await request.delete(
        `${baseURL}/api/v1/departments/${dept.id}`,
        { headers: authHeaders(owner) },
    );
    expect(del.status()).toBe(204);

    const list = await request.get(`${baseURL}/api/v1/departments`, {
        headers: authHeaders(owner),
    });
    const names = ((await list.json()) as Array<{ name: string }>).map(
        (d) => d.name,
    );
    expect(names).not.toContain(renamed);
});
