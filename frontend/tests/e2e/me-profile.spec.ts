/**
 * "Me" endpoints — profile PATCH + password change.
 *
 * The profile page has many sections (password, MFA, etc.) so we exercise
 * the underlying REST contracts and confirm the UI reflects the change.
 */
import { expect, test } from "@playwright/test";
import {
    authHeaders,
    bootstrapIdentity,
    loginExistingIdentity,
    randomSuffix,
    requireStack,
    seedSession,
} from "./helpers";

test.beforeEach(async ({ baseURL }) => {
    await requireStack(baseURL);
});

test("PATCH /me updates the displayed name", async ({
    baseURL,
    request,
    page,
}) => {
    const identity = await bootstrapIdentity(request, baseURL!);
    const newName = `E2E Renamed ${randomSuffix()}`;

    const patch = await request.patch(`${baseURL}/api/v1/me`, {
        headers: authHeaders(identity),
        data: { name: newName },
    });
    expect(patch.ok(), `patch status ${patch.status()}`).toBe(true);

    await seedSession(page, identity);
    await page.goto("/en-US/settings/profile");
    await expect(
        page.getByRole("heading", { level: 1, name: "Profile" }),
    ).toBeVisible({ timeout: 15_000 });
    // The profile Name input reflects the new value.
    await expect(
        page.locator(`input[value="${newName}"]`).first(),
    ).toBeVisible({ timeout: 10_000 });
});

test("POST /me/password rotates the password; old one stops working", async ({
    baseURL,
    request,
}) => {
    const identity = await bootstrapIdentity(request, baseURL!);
    const newPassword = "e2e-password-rotated-very-long";

    const change = await request.post(`${baseURL}/api/v1/me/password`, {
        headers: authHeaders(identity),
        data: {
            old_password: identity.password,
            new_password: newPassword,
        },
    });
    expect(change.status()).toBe(204);

    // Old password must no longer authenticate.
    const oldLogin = await request.post(`${baseURL}/api/v1/auth/login`, {
        data: { email: identity.email, password: identity.password },
    });
    expect(oldLogin.ok(), "old password should be rejected").toBe(false);

    // New password works end-to-end.
    const relogin = await loginExistingIdentity(
        request,
        baseURL!,
        identity.email,
        newPassword,
    );
    expect(relogin.accessToken).toBeTruthy();
});
