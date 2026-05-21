/**
 * Skills — upload + list + delete.
 *
 * POST /api/v1/skills takes a slug + raw SKILL.md markdown (a thin
 * wrapper over the filesystem). We upload a minimal skill, confirm it
 * appears in both the list endpoint and the UI, then delete it.
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

test("workspace skill pack upload + list + delete", async ({
    baseURL,
    request,
    page,
}) => {
    const identity = await bootstrapIdentity(request, baseURL!);
    const slug = `e2e-skill-${randomSuffix()}`.toLowerCase();
    const content = [
        "---",
        `name: ${slug}`,
        `description: e2e skill pack ${slug}`,
        "---",
        "",
        "# E2E skill",
        "",
        "Return the literal text `OK`.",
    ].join("\n");

    const create = await request.post(`${baseURL}/api/v1/skills`, {
        headers: authHeaders(identity),
        data: { slug, content },
    });
    expect(create.status(), `create status ${create.status()}`).toBe(201);

    // List endpoint should include the new workspace skill.
    const list = await request.get(`${baseURL}/api/v1/skills`, {
        headers: authHeaders(identity),
    });
    expect(list.ok()).toBe(true);
    const slugs = (
        (await list.json()) as Array<{ slug: string; source: string }>
    )
        .filter((s) => s.source === "workspace")
        .map((s) => s.slug);
    expect(slugs).toContain(slug);

    // UI should render the skill somewhere on /skills.
    await seedSession(page, identity);
    await page.goto("/en-US/skills");
    await expect(
        page.getByRole("heading", { level: 1, name: "Skills" }),
    ).toBeVisible({ timeout: 15_000 });
    await expect(page.getByText(slug).first()).toBeVisible({
        timeout: 10_000,
    });

    // Delete.
    const del = await request.delete(
        `${baseURL}/api/v1/skills/workspace/${slug}`,
        { headers: authHeaders(identity) },
    );
    expect(del.status()).toBe(204);
});
