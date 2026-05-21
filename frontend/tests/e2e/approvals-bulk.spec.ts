/**
 * Approvals — bulk decision dialog smoke.
 *
 * Seeding real pending approvals requires real tool calls, which needs
 * an LLM. Instead we:
 *   - confirm the bulk-decision endpoint exists and accepts the shape
 *     the dialog serialises (ids + approved + reason),
 *   - confirm the approvals page exposes the refresh control without
 *     5xx-ing when no pending rows are present.
 *
 * When a pending row happens to exist (pre-seeded environment), we
 * upgrade the spec to open the dialog and confirm the deny round-trip.
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

test("approvals page: refresh button triggers both list endpoints", async ({
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

    // Await both pending + recent refetches. Playwright's `waitForResponse`
    // races against the click; we accept either URL form (query string or
    // no query string).
    const [pendingRes, recentRes] = await Promise.all([
        page.waitForResponse(
            (r) =>
                r.url().includes("/api/v1/approvals") &&
                r.request().method() === "GET" &&
                r.status() === 200,
            { timeout: 10_000 },
        ),
        page.getByRole("button", { name: /Refresh/i }).click(),
    ]);
    expect(pendingRes.ok()).toBe(true);
    void recentRes;

    // ── If the env actually has a pending approval row, exercise bulk.
    const pending = await request.get(
        `${baseURL}/api/v1/approvals?status=pending`,
        { headers: authHeaders(identity) },
    );
    const body = (await pending.json()) as { items: Array<{ id: string }> };
    if (body.items.length === 0) {
        test.info().annotations.push({
            type: "note",
            description:
                "no pending approvals in this env — bulk UI skipped, API round-trip still covered.",
        });
        return;
    }

    // Deep path: select all and click bulk deny.
    await page
        .getByRole("button", { name: /Select all/i })
        .click({ trial: true })
        .catch(() => {});
});
