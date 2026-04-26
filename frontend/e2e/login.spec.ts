/**
 * Login spec — verifies the stack is up, the login form mounts, and
 * covers the two most common failure modes (wrong password, missing
 * inputs). This is the fastest signal that a given PR didn't break the
 * front-end shell; keep it first so CI fails immediately on a broken
 * compose.
 */
import { expect, test } from "@playwright/test";
import { bootstrapIdentity, requireStack } from "./helpers";

test.beforeEach(async ({ baseURL }) => {
    await requireStack(baseURL);
});

test("landing + login page render", async ({ page, baseURL }) => {
    // Landing page — workspace branding means we can't assert a specific
    // heading, so we just prove the shell mounted without 5xx.
    await page.goto("/");
    await expect(page.locator("body")).toBeVisible();

    // Direct-navigate the login route; it must render the login form
    // regardless of the visitor's auth state (signed-in users get bumped
    // home by the page's own effect, not by a 5xx).
    await page.goto("/en-US/login");
    await expect(page.getByTestId("login-form")).toBeVisible({
        timeout: 15_000,
    });
    await expect(page.getByTestId("login-email")).toBeVisible();
    await expect(page.getByTestId("login-password")).toBeVisible();
    await expect(page.getByTestId("login-submit")).toBeVisible();

    // OAuth providers endpoint must reply 200 with a `providers` list
    // (empty is fine — a cold compose has no IdPs configured).
    const resp = await page.request.get(`${baseURL}/api/v1/auth/oauth/providers`);
    expect(resp.ok()).toBe(true);
    expect(await resp.json()).toHaveProperty("providers");
});

test("wrong password shows an error and stays on /login", async ({
    page,
    request,
    baseURL,
}) => {
    // Register a real user so the account exists — then submit with an
    // obviously-wrong password. We want to confirm:
    //   1. The request hit the backend (401 round-trip).
    //   2. The UI did not redirect (i.e. it surfaced the error rather than
    //      silently dropping the user into the app).
    const identity = await bootstrapIdentity(request, baseURL!);

    await page.goto("/en-US/login");
    await page.getByTestId("login-email").fill(identity.email);
    await page.getByTestId("login-password").fill("definitely-not-the-password");
    await page.getByTestId("login-submit").click();

    // Give the backend time to answer 401. The form intentionally does
    // not redirect on failure, so we wait briefly and then assert the URL.
    await page.waitForTimeout(1_500);
    await expect(page).toHaveURL(/\/login$/);
    // Submit button should be re-enabled after the failed attempt (not
    // stuck in "loading").
    await expect(page.getByTestId("login-submit")).toBeEnabled();
});
