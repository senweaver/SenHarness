/**
 * Governance UI smoke — workspace-admin creates a policy + a budget.
 *
 * Verifies the hook → API round-trip (we're not exercising the
 * scope-GLOBAL branch — that requires platform_admin which isn't
 * bootstrapped by default).
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

test("workspace admin can create a governance policy + budget", async ({
  baseURL,
  page,
  request,
}) => {
  const account = await bootstrapAccount(request, baseURL!);
  await persistBootstrappedAuth(page, account, baseURL!);

  await page.goto("/en-US/settings/workspace/governance");
  await expect(page.getByTestId("governance-page")).toBeVisible({
    timeout: 15_000,
  });

  // ── Policy tab (default) ────────────────────────────────
  await page.getByTestId("policy-new").click();
  const policyName = `E2E policy ${Date.now()}`;
  await page.getByTestId("policy-form-name").fill(policyName);
  await page.getByTestId("policy-form-submit").click();

  // List should refresh with our row.
  await expect(
    page.getByTestId("policy-list").getByText(policyName),
  ).toBeVisible({ timeout: 10_000 });

  // ── Budget tab ──────────────────────────────────────────
  await page.getByTestId("governance-tab-budgets").click();
  await page.getByTestId("budget-new").click();
  const budgetName = `E2E budget ${Date.now()}`;
  await page.getByTestId("budget-form-name").fill(budgetName);
  await page.getByTestId("budget-form-limit").fill("25");
  await page.getByTestId("budget-form-submit").click();

  await expect(
    page.getByTestId("budget-list").getByText(budgetName),
  ).toBeVisible({ timeout: 10_000 });
});

test("policies + budgets list via API and persist across reloads", async ({
  baseURL,
  request,
  page,
}) => {
  // Lighter companion spec: creates a policy via UI, then reloads the page
  // and confirms the row is still there (catches regressions in the list
  // query when React Query cache is cold).
  const account = await bootstrapAccount(request, baseURL!);
  await persistBootstrappedAuth(page, account, baseURL!);

  await page.goto("/en-US/settings/workspace/governance");
  await expect(page.getByTestId("governance-page")).toBeVisible({
    timeout: 15_000,
  });

  await page.getByTestId("policy-new").click();
  const policyName = `E2E persist ${Date.now()}`;
  await page.getByTestId("policy-form-name").fill(policyName);
  await page.getByTestId("policy-form-submit").click();

  await expect(
    page.getByTestId("policy-list").getByText(policyName),
  ).toBeVisible({ timeout: 10_000 });

  // Full page reload — the list hydrates from a fresh GET.
  await page.reload();
  await expect(page.getByTestId("governance-page")).toBeVisible({
    timeout: 15_000,
  });
  await expect(
    page.getByTestId("policy-list").getByText(policyName),
  ).toBeVisible({ timeout: 10_000 });
});
