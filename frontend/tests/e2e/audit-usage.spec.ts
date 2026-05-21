/**
 * Audit log + Usage page smokes, with actual seeded activity.
 *
 * Creating an agent emits an `agent.create` audit row. Usage defaults to
 * an empty report if there are no metered messages, which is fine — we
 * only verify the page mounts + the API endpoint returns OK.
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

test("audit log shows agent.create event after seeding an agent", async ({
    baseURL,
    request,
    page,
}) => {
    const identity = await bootstrapIdentity(request, baseURL!);
    await apiCreateAgent(request, baseURL!, identity);

    const events = await request.get(`${baseURL}/api/v1/audit/events`, {
        headers: authHeaders(identity),
    });
    expect(events.ok()).toBe(true);
    const rows = (await events.json()) as Array<{ action: string }>;
    expect(rows.map((r) => r.action)).toContain("agent.create");

    await seedSession(page, identity);
    await page.goto("/en-US/settings/audit", { waitUntil: "domcontentloaded" });
    await expect(
        page.getByRole("heading", { level: 1, name: "Audit log" }),
    ).toBeVisible({ timeout: 30_000 });
    // One of the agent.create rows should render in the list view.
    await expect(page.getByText("agent.create").first()).toBeVisible({
        timeout: 15_000,
    });
});

test("usage endpoint responds with a valid shape", async ({
    baseURL,
    request,
    page,
}) => {
    const identity = await bootstrapIdentity(request, baseURL!);
    const usage = await request.get(
        `${baseURL}/api/v1/metrics/usage?window=7d`,
        { headers: authHeaders(identity) },
    );
    expect(usage.ok(), `usage status ${usage.status()}`).toBe(true);
    const body = await usage.json();
    // We don't care what the numbers are, just that the envelope shape is
    // valid JSON.
    expect(typeof body).toBe("object");

    await seedSession(page, identity);
    await page.goto("/en-US/settings/usage", { waitUntil: "domcontentloaded" });
    await expect(
        page.getByRole("heading", { level: 1, name: "Usage & cost" }),
    ).toBeVisible({ timeout: 30_000 });
});
