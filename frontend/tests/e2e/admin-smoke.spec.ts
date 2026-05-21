/**
 * Admin console smoke — mount + h1 on every admin page.
 *
 * `bootstrapPlatformAdmin()` skips the spec gracefully when there is no
 * platform_admin seed available (see helpers). Set the following env
 * vars when running e2e against a fixtures DB:
 *
 *     E2E_PLATFORM_ADMIN_EMAIL=admin@example.com
 *     E2E_PLATFORM_ADMIN_PASSWORD=the-password
 */
import { test } from "@playwright/test";
import {
    bootstrapPlatformAdmin,
    gotoAndExpectH1,
    requireStack,
    seedAdminSession,
} from "./helpers";

test.beforeEach(async ({ baseURL }) => {
    await requireStack(baseURL);
});

interface AdminPage {
    url: string;
    heading: string | RegExp;
}

const PAGES: AdminPage[] = [
    { url: "/en-US/admin", heading: "Platform overview" },
    { url: "/en-US/admin/users", heading: "Users" },
    { url: "/en-US/admin/governance", heading: "Global governance" },
    { url: "/en-US/admin/approvals", heading: "Cross-workspace approvals" },
    { url: "/en-US/admin/observability", heading: "Observability" },
    { url: "/en-US/admin/workspaces", heading: "Workspaces" },
    { url: "/en-US/admin/keyring", heading: "KEK Keyring" },
];

for (const { url, heading } of PAGES) {
    test(`admin smoke: ${url}`, async ({ baseURL, request, page }) => {
        const admin = await bootstrapPlatformAdmin(request, baseURL!);
        await seedAdminSession(page, admin);
        await gotoAndExpectH1(page, url, heading);
    });
}
