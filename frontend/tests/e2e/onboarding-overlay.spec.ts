/**
 * Onboarding overlay smoke spec — registers a fresh user, lands on the
 * dashboard with ``?onboarding=1`` so the overlay mounts, walks through
 * welcome → workspace → skip provider → skip agent → done, and asserts
 * the backend stamped ``identities.onboarded_at`` via /api/v1/me.
 */
import { expect, test } from "@playwright/test";
import {
  bootstrapIdentity,
  requireStack,
  seedSession,
} from "./helpers";

test.beforeEach(async ({ baseURL }) => {
  await requireStack(baseURL);
});

test("new user can run the onboarding overlay end-to-end", async ({
  baseURL,
  request,
  page,
}) => {
  const identity = await bootstrapIdentity(request, baseURL!);
  await seedSession(page, identity);

  await page.goto("/en-US/?onboarding=1");

  await expect(page.getByRole("heading", { level: 2 })).toBeVisible({
    timeout: 15_000,
  });

  await page.getByRole("button", { name: /get started|开始/i }).click();
  await page.getByRole("button", { name: /next|下一步/i }).click();
  await page.getByRole("button", { name: /skip/i }).first().click();
  await page.getByRole("button", { name: /skip/i }).first().click();
  await page
    .getByRole("button", { name: /go to dashboard|chat with my/i })
    .click();

  await expect.poll(async () => {
    const me = await request.get(`${baseURL}/api/v1/me`, {
      headers: { Authorization: `Bearer ${identity.accessToken}` },
    });
    if (!me.ok()) return null;
    const body = (await me.json()) as { onboarded_at: string | null };
    return body.onboarded_at;
  }).not.toBeNull();
});
