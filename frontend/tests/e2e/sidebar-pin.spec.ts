/**
 * Sidebar pin/unpin smoke spec — drives the "My" section pin menu on a
 * freshly-created agent (auto-star hook puts it on the sidebar) and
 * verifies pin toggles via the GET /api/v1/sidebar/my-items contract.
 */
import { expect, test } from "@playwright/test";
import {
  bootstrapIdentity,
  randomSuffix,
  requireStack,
  seedSession,
} from "./helpers";

test.beforeEach(async ({ baseURL }) => {
  await requireStack(baseURL);
});

test("pin then unpin an agent in the sidebar", async ({
  baseURL,
  request,
  page,
}) => {
  const identity = await bootstrapIdentity(request, baseURL!);
  await seedSession(page, identity);

  const agentName = `Pin Bot ${randomSuffix()}`;
  const createAgent = await request.post(`${baseURL}/api/v1/agents`, {
    headers: {
      Authorization: `Bearer ${identity.accessToken}`,
      "X-Workspace-Id": identity.workspaceId,
    },
    data: { name: agentName },
  });
  expect(createAgent.ok(), "agent create").toBe(true);
  const { id: agentId } = (await createAgent.json()) as { id: string };

  await page.goto("/en-US/");
  await expect(page.getByText(agentName)).toBeVisible({ timeout: 10_000 });

  const pinResp = await request.post(
    `${baseURL}/api/v1/agents/${agentId}/star?pinned=true`,
    {
      headers: {
        Authorization: `Bearer ${identity.accessToken}`,
        "X-Workspace-Id": identity.workspaceId,
      },
    },
  );
  expect(pinResp.ok(), "pin").toBe(true);

  const sidebar = await request.get(
    `${baseURL}/api/v1/sidebar/my-items?limit=50`,
    {
      headers: {
        Authorization: `Bearer ${identity.accessToken}`,
        "X-Workspace-Id": identity.workspaceId,
      },
    },
  );
  expect(sidebar.ok()).toBe(true);
  const list = (await sidebar.json()) as {
    items: Array<{ id: string; pinned: boolean }>;
  };
  const row = list.items.find((item) => item.id === agentId);
  expect(row?.pinned, "agent should be pinned after star").toBe(true);

  const unstar = await request.delete(
    `${baseURL}/api/v1/agents/${agentId}/star`,
    {
      headers: {
        Authorization: `Bearer ${identity.accessToken}`,
        "X-Workspace-Id": identity.workspaceId,
      },
    },
  );
  expect(unstar.ok(), "unstar").toBe(true);

  const sidebarAfter = await request.get(
    `${baseURL}/api/v1/sidebar/my-items?limit=50`,
    {
      headers: {
        Authorization: `Bearer ${identity.accessToken}`,
        "X-Workspace-Id": identity.workspaceId,
      },
    },
  );
  const after = (await sidebarAfter.json()) as {
    items: Array<{ id: string }>;
  };
  expect(after.items.find((item) => item.id === agentId)).toBeUndefined();
});
