/**
 * 404 fix: `next-intl` matcher used to drop any path with a dot, which
 * killed deep-links like `/admin/settings/auth.registration` under the
 * default locale (no `/en-US/` prefix) — the regex saw `auth.registration`
 * and classified it as a static asset request.
 *
 * The fix tightens the matcher to a whitelist of real asset extensions
 * anchored with `$`. Verify by hitting the bare URL and asserting we
 * don't end up on the Next.js 404 chrome.
 *
 * The page itself is auth-gated, so we don't bother seeding a session —
 * we only care that the **middleware** routes the request. Acceptable
 * outcomes when unauthenticated:
 *   • 200 from `/en-US/admin/settings/auth.registration` (auth check is
 *     client-side via Zustand; SSR renders the app shell), or
 *   • a 30x redirect into the locale-prefixed URL / login flow.
 *
 * The regression we're guarding against is a hard 404 (status 404 OR the
 * "Page not found" / "404" Next.js error chrome).
 */
import { expect, test } from "@playwright/test";

import { requireStack } from "./helpers";

test.beforeEach(async ({ baseURL }) => {
    await requireStack(baseURL);
});

test("default-locale dotted admin URL is routed (not 404)", async ({
    baseURL,
    page,
}) => {
    const response = await page.goto(
        `${baseURL}/admin/settings/auth.registration`,
        { waitUntil: "domcontentloaded" },
    );

    // The middleware should rewrite (or redirect) to the locale-prefixed
    // path. Either is fine; what we forbid is a hard 404.
    expect(response, "expected a response from /admin/settings/...").not.toBeNull();
    expect(response!.status(), "no Next.js 404 on the dotted path").not.toBe(404);

    // Belt-and-braces: the rendered body must not show the canonical
    // Next.js 404 chrome ("404 | This page could not be found").
    const body = await page.locator("body").innerText();
    expect(body, "rendered body should not be the Next.js 404 chrome").not.toMatch(
        /This page could not be found\.?/i,
    );

    // Final URL should reach the localized variant (default locale prefix
    // applied) — proof the matcher let the path through.
    await expect(page).toHaveURL(/\/admin\/settings\/auth\.registration/);
});
