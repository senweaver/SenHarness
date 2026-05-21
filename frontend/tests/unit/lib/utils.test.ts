/**
 * Smoke tests for lib/utils. Keeping at least one Vitest spec
 * in the tree so `pnpm test` has something to execute on a fresh
 * `pnpm install` — a 0-test green run is too easy to confuse with a
 * broken runner.
 */
import { describe, expect, it } from "vitest";
import { cn, relativeTime } from "@/lib/utils";

describe("cn", () => {
    it("joins plain class names", () => {
        expect(cn("a", "b")).toContain("a");
        expect(cn("a", "b")).toContain("b");
    });

    it("drops falsy values", () => {
        expect(cn("a", false, null, undefined, "")).toBe("a");
    });

    it("lets tailwind-merge resolve conflicting utilities", () => {
        // p-4 overrides p-2 — tailwind-merge keeps only the last.
        expect(cn("p-2", "p-4")).toBe("p-4");
    });
});

describe("relativeTime", () => {
    it("returns empty string for null input", () => {
        expect(relativeTime(null)).toBe("");
        expect(relativeTime(undefined)).toBe("");
    });

    it("produces something non-empty for a real ISO timestamp", () => {
        const past = new Date(Date.now() - 60_000).toISOString();
        const out = relativeTime(past, "en-US");
        expect(out.length).toBeGreaterThan(0);
    });

    it("honours the locale argument", () => {
        const past = new Date(Date.now() - 60_000).toISOString();
        const en = relativeTime(past, "en-US");
        const zh = relativeTime(past, "zh-CN");
        // Different locales produce different strings for the same
        // delta — catches accidentally hardcoded locales.
        expect(en).not.toBe(zh);
    });
});
