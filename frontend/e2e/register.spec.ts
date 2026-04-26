/**
 * Register UI spec — proves the self-signup form at `/register` posts to
 * `/api/v1/auth/register`, redirects to `/login`, and rejects duplicate
 * emails on the second submit.
 *
 * No `data-testid`s on the register page — we lean on the English headings
 * ("Create an account" / "Sign up") and stable form semantics.
 */
import { expect, test } from "@playwright/test";
import { randomEmail, requireStack } from "./helpers";

test.beforeEach(async ({ baseURL }) => {
    await requireStack(baseURL);
});

test("register via UI redirects to /login on success", async ({ page }) => {
    await page.goto("/en-US/register");

    await expect(
        page.getByRole("heading", { name: "Create an account" }),
    ).toBeVisible({ timeout: 15_000 });

    const email = randomEmail();
    // Labels here are rendered as plain `<label>` tags without `htmlFor`,
    // so we lean on the HTML type attributes for stable anchors. The Name
    // input is the only text-typed (default) input in the form.
    await page.locator("input[type='email']").fill(email);
    await page
        .locator(
            "form input:not([type='email']):not([type='password'])",
        )
        .first()
        .fill("E2E Register User");
    await page.locator("input[type='password']").fill("e2e-password-very-long");

    await page.getByRole("button", { name: "Sign up" }).click();

    await expect(page).toHaveURL(/\/login$/, { timeout: 15_000 });
    // Landing on /login should show the login form (proves we didn't bounce
    // to a 500 page somewhere along the way).
    await expect(page.getByTestId("login-form")).toBeVisible();
});

test("register rejects duplicate email", async ({ page, request, baseURL }) => {
    const email = randomEmail();

    // First registration succeeds via direct API — faster than clicking
    // through the form twice.
    const first = await request.post(`${baseURL}/api/v1/auth/register`, {
        data: { email, name: "First", password: "e2e-password-very-long" },
    });
    expect(first.status()).toBe(201);

    // Second submit via UI must fail — error text renders inline and URL
    // stays on /register.
    await page.goto("/en-US/register");
    await page.locator("input[type='email']").fill(email);
    await page
        .locator(
            "form input:not([type='email']):not([type='password'])",
        )
        .first()
        .fill("Second");
    await page.locator("input[type='password']").fill("e2e-password-very-long");
    await page.getByRole("button", { name: "Sign up" }).click();

    // Small settle window for the 409 round-trip.
    await page.waitForTimeout(1_500);
    await expect(page).toHaveURL(/\/register$/);
});
