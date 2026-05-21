/**
 * Danger zone — owner-only delete workspace smoke spec. The form requires
 * typing the exact workspace name before the destructive button enables,
 * so the spec drives the real input. After the API confirms deletion we
 * verify the workspace is no longer listed for the caller.
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

test("owner deletes workspace via danger zone", async ({
  baseURL,
  request,
  page,
}) => {
  const identity = await bootstrapIdentity(request, baseURL!);
  await seedSession(page, identity);

  await page.goto("/en-US/settings/workspace/branding");

  const deleteCta = page.getByRole("button", { name: /delete workspace/i });
  await expect(deleteCta).toBeVisible({ timeout: 15_000 });
  await deleteCta.click();

  const wsRes = await request.get(
    `${baseURL}/api/v1/workspaces/${identity.workspaceId}`,
    {
      headers: {
        Authorization: `Bearer ${identity.accessToken}`,
        "X-Workspace-Id": identity.workspaceId,
      },
    },
  );
  expect(wsRes.ok()).toBe(true);
  const { name } = (await wsRes.json()) as { name: string };

  await page
    .getByLabel(new RegExp(`type "${name}"`, "i"))
    .fill(name);
  await page.getByRole("button", { name: /delete forever/i }).click();

  await expect.poll(async () => {
    const list = await request.get(`${baseURL}/api/v1/workspaces`, {
      headers: { Authorization: `Bearer ${identity.accessToken}` },
    });
    if (!list.ok()) return null;
    const arr = (await list.json()) as Array<{ id: string }>;
    return arr.some((row) => row.id === identity.workspaceId);
  }).toBe(false);
});
