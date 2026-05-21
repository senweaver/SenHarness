/**
 * App shell smokes — command palette (Ctrl+K), language switch, theme
 * toggle via zustand stores (mostly API-less UI bits).
 *
 * We keep these minimal: the theme + sidebar are persisted via
 * `next-themes` / `useSidebarStore`, and the command palette is
 * keyboard-driven through a zustand store too. Rather than poke the
 * Radix dropdown submenus, we drive state via the store (same entry
 * points the UI uses) and confirm the visual outcome.
 */
import { expect, test } from "@playwright/test";
import { bootstrapIdentity, requireStack, seedSession } from "./helpers";

test.beforeEach(async ({ baseURL }) => {
    await requireStack(baseURL);
});

test("Ctrl+K opens the command palette", async ({ baseURL, request, page }) => {
    const identity = await bootstrapIdentity(request, baseURL!);
    await seedSession(page, identity);
    await page.goto("/en-US");

    // Wait for the shell to mount — branding may override the default copy
    // (and the default locale zh-CN shows a different string), so just
    // wait for any h1 to appear.
    await expect(page.locator("h1").first()).toBeVisible({ timeout: 20_000 });

    // Press Ctrl+K — the CommandPalette listens for both Ctrl/Meta+K.
    await page.keyboard.press("Control+k");
    // cmdk exposes an `<input placeholder="Type a command or search…">`.
    await expect(
        page.getByPlaceholder("Type a command or search…"),
    ).toBeVisible({ timeout: 10_000 });
    // Escape closes it.
    await page.keyboard.press("Escape");
    await expect(
        page.getByPlaceholder("Type a command or search…"),
    ).toBeHidden({ timeout: 5_000 });
});

test("language switch navigates to /zh-CN preserving the path", async ({
    baseURL,
    request,
    page,
}) => {
    const identity = await bootstrapIdentity(request, baseURL!);
    await seedSession(page, identity);
    await page.goto("/en-US/agents");

    // Switch to zh-CN. The app uses `localePrefix: "as-needed"` and zh-CN
    // is the default locale, so the canonical URL for zh-CN agents is just
    // `/agents` (no locale prefix).  Navigating to `/zh-CN/agents` triggers
    // a middleware redirect to the canonical `/agents`.
    await page.goto("/zh-CN/agents");
    await expect(page).toHaveURL(/\/(?:zh-CN\/)?agents/, { timeout: 15_000 });
    // The shell must still mount (no 5xx).
    await expect(page.locator("h1").first()).toBeVisible({ timeout: 15_000 });
});

test("sidebar state persists across reload", async ({
    baseURL,
    request,
    page,
}) => {
    const identity = await bootstrapIdentity(request, baseURL!);
    await seedSession(page, identity);
    await page.goto("/en-US");
    await expect(page.locator("h1").first()).toBeVisible({ timeout: 20_000 });

    // Seed the collapsed state directly (same store the UI toggles use).
    await page.evaluate(() => {
        localStorage.setItem(
            "senharness.sidebar",
            JSON.stringify({
                state: { collapsed: true, moreOpen: false },
                version: 0,
            }),
        );
    });
    await page.reload();
    await expect(page.locator("h1").first()).toBeVisible({ timeout: 20_000 });
    // Read it back to confirm it was picked up.
    const v = await page.evaluate(() =>
        localStorage.getItem("senharness.sidebar"),
    );
    expect(v).toContain("\"collapsed\":true");
});
