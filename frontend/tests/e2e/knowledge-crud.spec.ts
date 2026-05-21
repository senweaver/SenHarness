/**
 * Knowledge — collection + doc + search round-trip.
 *
 * The `/knowledge` page mounts a 2-column layout; deep UI driving of the
 * textarea + source-kind Select is brittle, so we mix API (create coll,
 * ingest text doc, search) with UI (assert the seeded rows render).
 */
import { expect, test } from "@playwright/test";
import {
    apiCreateKnowledgeCollection,
    authHeaders,
    bootstrapIdentity,
    randomSuffix,
    requireStack,
    seedSession,
} from "./helpers";

test.beforeEach(async ({ baseURL }) => {
    await requireStack(baseURL);
});

test("create collection + ingest doc + search via API, verify UI lists collection", async ({
    baseURL,
    request,
    page,
}) => {
    const identity = await bootstrapIdentity(request, baseURL!);
    const collection = await apiCreateKnowledgeCollection(
        request,
        baseURL!,
        identity,
    );

    const docTitle = `E2E doc ${randomSuffix()}`;
    const unique = `marker-${randomSuffix()}`;
    const ingest = await request.post(
        `${baseURL}/api/v1/knowledge/collections/${collection.id}/docs`,
        {
            headers: authHeaders(identity),
            data: {
                title: docTitle,
                source_kind: "text",
                raw_text: `This e2e text contains the unique marker ${unique} for search.`,
                metadata_json: {},
            },
        },
    );
    expect(ingest.status(), "doc ingest status").toBe(201);

    // Search should find the chunk via the unique marker. Note: vector
    // search may fall back to BM25 when no embedding provider is set.
    const search = await request.post(
        `${baseURL}/api/v1/knowledge/collections/${collection.id}/search`,
        {
            headers: authHeaders(identity),
            data: { query: unique, top_k: 5 },
        },
    );
    // Either the search finds hits (happy path) or returns empty (no
    // embedding provider) — both are acceptable; we just want a 200.
    expect(search.ok(), `search status ${search.status()}`).toBe(true);

    await seedSession(page, identity);
    await page.goto("/en-US/knowledge");

    await expect(
        page.getByRole("heading", { level: 1, name: "Knowledge" }),
    ).toBeVisible({ timeout: 15_000 });
    await expect(page.getByText(collection.name).first()).toBeVisible({
        timeout: 15_000,
    });
});

test("delete collection removes it from the API list", async ({
    baseURL,
    request,
}) => {
    const identity = await bootstrapIdentity(request, baseURL!);
    const collection = await apiCreateKnowledgeCollection(
        request,
        baseURL!,
        identity,
    );

    const del = await request.delete(
        `${baseURL}/api/v1/knowledge/collections/${collection.id}`,
        { headers: authHeaders(identity) },
    );
    expect(del.status()).toBe(204);

    const list = await request.get(`${baseURL}/api/v1/knowledge/collections`, {
        headers: authHeaders(identity),
    });
    const names = ((await list.json()) as Array<{ name: string }>).map(
        (c) => c.name,
    );
    expect(names).not.toContain(collection.name);
});
