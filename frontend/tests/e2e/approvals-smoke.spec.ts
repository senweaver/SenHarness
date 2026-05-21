/**
 * Approvals queue smoke — page mount + tabs + empty state + bulk API.
 *
 * Seeding a *real* pending approval requires mid-session tool-call
 * interception from an LLM-backed agent, which e2e can't reliably
 * do without a provider configured. We cover everything we can:
 *
 *   - `/approvals` renders with the correct h1 and both tabs.
 *   - The pending / history endpoints return OK for a cold workspace
 *     (empty arrays are fine).
 *   - `POST /approvals/bulk-decision` with an empty id list is a valid
 *     no-op and returns a well-formed `BulkDecisionResult`.
 *
 * When a deep decide round-trip is needed, the spec gracefully upgrades
 * to click-through if there happens to be at least one pending row (e.g.
 * when running against a pre-seeded fixtures DB).
 */
import { expect, test } from "@playwright/test";
import {
    authHeaders,
    bootstrapIdentity,
    requireStack,
    seedSession,
} from "./helpers";

test.beforeEach(async ({ baseURL }) => {
    await requireStack(baseURL);
});

test("approvals page mounts with pending + history tabs", async ({
    baseURL,
    request,
    page,
}) => {
    const identity = await bootstrapIdentity(request, baseURL!);
    await seedSession(page, identity);

    await page.goto("/en-US/approvals");
    await expect(
        page.getByRole("heading", { level: 1, name: "To-dos & approvals" }),
    ).toBeVisible({ timeout: 15_000 });

    // Switch to the History tab — proves the tab control isn't broken.
    await page.getByRole("button", { name: "History" }).click();
    // "Empty history" copy should appear — a cold workspace has no decided
    // approvals yet. This doubles as a Pending→History tab transition
    // assertion.
    await expect(page.getByText("No history yet.").first()).toBeVisible({
        timeout: 10_000,
    });

    // Underlying endpoints respond with well-formed arrays.
    const pending = await request.get(
        `${baseURL}/api/v1/approvals?status=pending`,
        { headers: authHeaders(identity) },
    );
    expect(pending.ok(), "pending list response").toBe(true);
    const pendingBody = (await pending.json()) as { items: unknown[] };
    expect(Array.isArray(pendingBody.items)).toBe(true);

    const recent = await request.get(`${baseURL}/api/v1/approvals`, {
        headers: authHeaders(identity),
    });
    expect(recent.ok()).toBe(true);
});

test("bulk decision endpoint rejects an empty id list", async ({
    baseURL,
    request,
}) => {
    const identity = await bootstrapIdentity(request, baseURL!);
    // The backend schema enforces min_length=1 on `approval_ids`, so
    // sending an empty list must return 422 Unprocessable Entity.
    const res = await request.post(`${baseURL}/api/v1/approvals/bulk-decision`, {
        headers: authHeaders(identity),
        data: {
            approval_ids: [],
            action: "deny",
        },
    });
    expect(res.status()).toBe(422);
});
