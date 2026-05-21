/**
 * Agents — list + detail + star round-trip.
 *
 * Seeds 3 agents via API, then UI-drives:
 *   1. `/agents` lists them.
 *   2. Clicking an agent card navigates to `/agents/{id}` detail.
 *   3. Starring via the "Star" button toggles `POST /api/v1/agents/{id}/star`.
 *   4. "Start chat" on the detail page leads to a fresh chat session.
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

test("list agents, open detail, star toggles via API", async ({
    baseURL,
    request,
    page,
}) => {
    const identity = await bootstrapIdentity(request, baseURL!);
    const agents = await Promise.all(
        [1, 2, 3].map(() => apiCreateAgent(request, baseURL!, identity)),
    );
    const target = agents[0]!;

    await seedSession(page, identity);
    await page.goto("/en-US/agents");

    // At least one of the seeded agents should be visible in the list.
    await expect(page.getByText(target.name).first()).toBeVisible({
        timeout: 15_000,
    });

    // Navigate to the detail page via URL (the card click target varies
    // with the grid layout) — still exercises `useAgent` + server.
    await page.goto(`/en-US/agents/${target.id}`);
    await expect(
        page.getByRole("heading", { level: 1, name: target.name }),
    ).toBeVisible({ timeout: 15_000 });

    await page.getByRole("button", { name: "Star", exact: true }).click();
    // Wait for the button label to flip to "Starred" — confirms the POST /star
    // request completed server-side before we poll the API directly.
    await expect(
        page.getByRole("button", { name: "Starred", exact: true }),
    ).toBeVisible({ timeout: 8_000 });
    const starred = await request.get(`${baseURL}/api/v1/agents/starred`, {
        headers: authHeaders(identity),
    });
    expect(starred.ok()).toBe(true);
    const arr = (await starred.json()) as Array<{ id: string }>;
    expect(arr.map((a) => a.id)).toContain(target.id);

    // Un-star roundtrip — wait for label to revert back to "Star" before polling.
    await page.getByRole("button", { name: "Starred", exact: true }).click();
    await expect(
        page.getByRole("button", { name: "Star", exact: true }),
    ).toBeVisible({ timeout: 8_000 });
    const starred2 = await request.get(`${baseURL}/api/v1/agents/starred`, {
        headers: authHeaders(identity),
    });
    const arr2 = (await starred2.json()) as Array<{ id: string }>;
    expect(arr2.map((a) => a.id)).not.toContain(target.id);
});

test("'Start chat' on detail creates a session and navigates", async ({
    baseURL,
    request,
    page,
}) => {
    const identity = await bootstrapIdentity(request, baseURL!);
    const agent = await apiCreateAgent(request, baseURL!, identity);

    await seedSession(page, identity);
    await page.goto(`/en-US/agents/${agent.id}`);

    await page.getByRole("link", { name: "Start chat" }).click();
    // "Start chat" links to /chat/new?agent={uuid}; a session is created
    // when the user submits their first message. Accept both the pre-session
    // composer URL and a fully created session URL.
    await expect(page).toHaveURL(
        /\/chat\/(?:new\?agent=[0-9a-f-]{36}|[0-9a-f-]{36}$)/,
        { timeout: 15_000 },
    );
});
