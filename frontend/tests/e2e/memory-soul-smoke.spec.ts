/**
 * USER.md + SOUL.md UI smoke.
 *
 * Covers the self-editable path (USER.md save) and the propose →
 * approve flow for SOUL.md — both are high-signal: a broken
 * propose/approve cycle means a user can lose control of what agents
 * infer about them.
 */
import { expect, test } from "@playwright/test";
import {
  bootstrapAccount,
  persistBootstrappedAuth,
  requireStack,
} from "./helpers";

test.beforeEach(async ({ baseURL }) => {
  await requireStack(baseURL!);
});

test("user can edit USER.md and approve a SOUL proposal", async ({
  baseURL,
  page,
  request,
}) => {
  const account = await bootstrapAccount(request, baseURL!);
  await persistBootstrappedAuth(page, account, baseURL!);

  await page.goto("/en-US/settings/profile/soul");
  await expect(page.getByTestId("soul-page")).toBeVisible({ timeout: 15_000 });

  // ── USER.md edit ────────────────────────────────────────
  await page
    .getByTestId("user-profile-textarea")
    .fill(
      "# About me\n\n- I prefer concise, actionable answers.\n- Favourite language: TypeScript.",
    );
  await page.getByTestId("user-profile-save").click();
  // Sonner toast lands on successful save.
  await expect(page.getByText(/Saved/i).first()).toBeVisible({
    timeout: 10_000,
  });

  // ── SOUL.md propose + approve round-trip ────────────────
  await page
    .getByTestId("soul-propose-content")
    .fill(
      "# SOUL update\n\n- Communication style: concise, technical\n- Goals: ship e2e coverage\n",
    );
  await page
    .getByTestId("soul-propose-rationale")
    .fill("Captured from e2e session");
  await page.getByTestId("soul-propose-submit").click();

  // The pending list should now contain our proposal with an
  // approve button — no exact id to match on, so we target the
  // first approve.
  const approveBtn = page.locator('[data-testid^="soul-approve-"]').first();
  await expect(approveBtn).toBeVisible({ timeout: 10_000 });
  await approveBtn.click();

  await expect(page.getByText(/Approved/i).first()).toBeVisible({
    timeout: 10_000,
  });
});
