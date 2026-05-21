/**
 * Settings long-tail smoke — walks every settings subpage and asserts
 * the main `<h1>` lands. Data-driven so adding a new subpage is a
 * one-line change.
 *
 * The headings below come from `frontend/messages/en-US.json` —
 * grepping for ``"title":`` under each ``settings.*`` namespace. We use
 * exact strings where the copy is stable and regexes where the title is
 * composed from multiple translation keys.
 */
import { test } from "@playwright/test";
import {
    bootstrapIdentity,
    gotoAndExpectH1,
    requireStack,
    seedSession,
} from "./helpers";

test.beforeEach(async ({ baseURL }) => {
    await requireStack(baseURL);
});

interface SettingsPage {
    url: string;
    heading: string | RegExp;
}

const PAGES: SettingsPage[] = [
    { url: "/en-US/settings/profile", heading: "Profile" },
    { url: "/en-US/settings/appearance", heading: "Appearance" },
    { url: "/en-US/settings/shortcuts", heading: "Keyboard shortcuts" },
    { url: "/en-US/settings/usage", heading: "Usage & cost" },
    { url: "/en-US/settings/audit", heading: "Audit log" },
    { url: "/en-US/settings/secrets", heading: /Secrets/ },
    { url: "/en-US/settings/moderation", heading: "Marketplace moderation" },
    { url: "/en-US/settings/billing", heading: "Credits & billing" },
    { url: "/en-US/settings/approvals", heading: "Approvals archive" },
    { url: "/en-US/settings/workspace/branding", heading: /Branding/ },
];

for (const { url, heading } of PAGES) {
    test(`settings smoke: ${url}`, async ({ baseURL, request, page }) => {
        const identity = await bootstrapIdentity(request, baseURL!);
        await seedSession(page, identity);
        await gotoAndExpectH1(page, url, heading);
    });
}
