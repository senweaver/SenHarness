/**
 * Agents — runtime switch + compare API smoke.
 *
 * The UI for `/api/v1/agents/{id}/runtime/switch` is currently surfaced
 * through the **edit form** (the `backend_kind` select triggers a PATCH),
 * while `runtime/compare` sits on `CompareRuntimesCard` inside the
 * workspace runtimes settings page. Since the explicit "switch" button
 * isn't on the detail page, we drive the switch through API (same as a
 * client-side hook would) and assert the detail page's runtime card
 * picks up the new backend.
 *
 * Graceful-skip branches:
 *   - `runtimes` endpoint reports fewer than 2 backends available
 *     (cold compose only ships `native`) → skip the switch half of the
 *     spec because there's nothing to switch to.
 *   - `runtime/compare` requires an actual LLM provider → skip if the
 *     response surfaces a provider-missing error.
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

test("runtime switch round-trip reflects in agent detail", async ({
    baseURL,
    request,
    page,
}) => {
    const identity = await bootstrapIdentity(request, baseURL!);
    const agent = await apiCreateAgent(request, baseURL!, identity, {
        backend_kind: "native",
    });

    // Pick an alternative runtime from the public registry. A cold
    // compose sometimes ships only `native`, in which case there's no
    // meaningful switch to make — skip gracefully.
    const runtimesRes = await request.get(`${baseURL}/api/v1/agents/runtimes`);
    expect(runtimesRes.ok()).toBe(true);
    const payload = await runtimesRes.json();
    const registered: Array<{ kind: string; requires_adapter: boolean }> =
        Array.isArray(payload) ? payload : (payload.runtimes ?? []);
    const other = registered.find(
        (r) => r.kind !== "native" && !r.requires_adapter,
    );
    if (!other) {
        test.skip(
            true,
            `only one runtime available (${registered.map((r) => r.kind).join(",")}); ` +
                "runtime-switch spec needs at least two adapter-less backends.",
        );
        return;
    }

    const switchRes = await request.post(
        `${baseURL}/api/v1/agents/${agent.id}/runtime/switch`,
        {
            headers: authHeaders(identity),
            data: { backend_kind: other.kind },
        },
    );
    expect(switchRes.ok(), "runtime switch response").toBe(true);

    // Detail page should now show the new backend badge.
    await seedSession(page, identity);
    await page.goto(`/en-US/agents/${agent.id}`);
    await expect(
        page.getByRole("heading", { level: 1, name: agent.name }),
    ).toBeVisible({ timeout: 15_000 });
    await expect(page.getByText(other.kind).first()).toBeVisible({
        timeout: 10_000,
    });
});

test("runtime compare endpoint responds with candidate shape", async ({
    baseURL,
    request,
}) => {
    const identity = await bootstrapIdentity(request, baseURL!);
    const agent = await apiCreateAgent(request, baseURL!, identity);

    const runtimesRes = await request.get(`${baseURL}/api/v1/agents/runtimes`);
    const payload = await runtimesRes.json();
    const registered: Array<{ kind: string; requires_adapter: boolean }> =
        Array.isArray(payload) ? payload : (payload.runtimes ?? []);
    const kinds = registered
        .filter((r) => !r.requires_adapter)
        .map((r) => r.kind)
        .slice(0, 2);
    if (kinds.length < 1) {
        test.skip(true, "no non-adapter runtimes — cannot call compare.");
        return;
    }

    const res = await request.post(
        `${baseURL}/api/v1/agents/${agent.id}/runtime/compare`,
        {
            headers: authHeaders(identity),
            data: {
                prompt: "Respond with the literal word OK.",
                runtimes: kinds,
                include_eval: false,
            },
        },
    );

    if (!res.ok()) {
        const text = await res.text().catch(() => "");
        if (/provider|model|unavailable|rate_limit|no model/i.test(text)) {
            test.skip(
                true,
                `compare API needs an LLM provider — got: ${text.slice(0, 160)}`,
            );
            return;
        }
        expect(res.ok(), `compare response: ${text.slice(0, 160)}`).toBe(true);
    }
    const body = (await res.json()) as {
        candidates: Array<{ runtime: string; ok: boolean }>;
    };
    expect(Array.isArray(body.candidates)).toBe(true);
    expect(body.candidates.length).toBeGreaterThan(0);
});
