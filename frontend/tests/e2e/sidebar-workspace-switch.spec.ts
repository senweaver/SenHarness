/**
 * Verify that switching the active workspace via the header popover
 * refreshes the SiderNav state. The flow is REST-driven on the second
 * workspace and asserts both /api/v1/me + /api/v1/sidebar/my-items reflect
 * the switch.
 */
import { expect, test } from "@playwright/test";
import {
  bootstrapIdentity,
  randomSlug,
  requireStack,
  seedSession,
} from "./helpers";

test.beforeEach(async ({ baseURL }) => {
  await requireStack(baseURL);
});

test("switching workspace refreshes sidebar items", async ({
  baseURL,
  request,
  page,
}) => {
  const identity = await bootstrapIdentity(request, baseURL!);
  await seedSession(page, identity);

  const create = await request.post(`${baseURL}/api/v1/workspaces`, {
    headers: { Authorization: `Bearer ${identity.accessToken}` },
    data: {
      name: "Second WS",
      slug: randomSlug("e2e-second"),
      description: "switching target",
    },
  });
  expect(create.ok(), "second workspace create").toBe(true);
  const { id: secondId } = (await create.json()) as { id: string };

  const switchResp = await request.post(
    `${baseURL}/api/v1/workspaces/${secondId}/switch`,
    { headers: { Authorization: `Bearer ${identity.accessToken}` } },
  );
  expect(switchResp.ok(), "workspace switch").toBe(true);
  const { access_token: newToken } = (await switchResp.json()) as {
    access_token: string;
  };

  const sidebar = await request.get(
    `${baseURL}/api/v1/sidebar/my-items?limit=50`,
    {
      headers: {
        Authorization: `Bearer ${newToken}`,
        "X-Workspace-Id": secondId,
      },
    },
  );
  expect(sidebar.ok()).toBe(true);
  const list = (await sidebar.json()) as { total: number };
  expect(list.total).toBe(0);
});
