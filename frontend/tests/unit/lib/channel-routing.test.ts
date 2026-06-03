/**
 * Tests for the channel-routing UI helpers (P1). These back the
 * layered-binding editor (most-specific-wins ordering, value-required
 * gating) and the handoff-rule keyword field, so a regression here would
 * surface as a UI that lets operators author rules the backend can't
 * resolve the way the screen implies.
 */
import { describe, expect, it } from "vitest";

import type { ChannelBinding } from "@/hooks/use-channels";
import {
  BINDING_SPECIFICITY,
  joinKeywords,
  parseKeywords,
  requiresMatchValue,
  scopeRefKind,
  sortBindingsBySpecificity,
} from "@/lib/channel-routing";

function binding(over: Partial<ChannelBinding>): ChannelBinding {
  return {
    id: over.id ?? Math.random().toString(36).slice(2),
    channel_id: "ch1",
    match_scope: "peer",
    match_value: "x",
    bind_scope: null,
    scope_ref_id: null,
    target_agent_id: null,
    allowlist_agent_ids: null,
    priority: 0,
    created_at: "2026-01-01T00:00:00Z",
    updated_at: "2026-01-01T00:00:00Z",
    ...over,
  };
}

describe("requiresMatchValue", () => {
  it("is false only for the channel_default rung", () => {
    expect(requiresMatchValue("channel_default")).toBe(false);
    expect(requiresMatchValue("peer")).toBe(true);
    expect(requiresMatchValue("group")).toBe(true);
  });
});

describe("BINDING_SPECIFICITY", () => {
  it("ranks peer above group above channel_default", () => {
    expect(BINDING_SPECIFICITY.peer).toBeGreaterThan(BINDING_SPECIFICITY.group);
    expect(BINDING_SPECIFICITY.group).toBeGreaterThan(
      BINDING_SPECIFICITY.channel_default,
    );
  });
});

describe("sortBindingsBySpecificity", () => {
  it("orders most-specific first", () => {
    const rows = [
      binding({ id: "def", match_scope: "channel_default", match_value: null }),
      binding({ id: "peer", match_scope: "peer" }),
      binding({ id: "group", match_scope: "group" }),
    ];
    expect(sortBindingsBySpecificity(rows).map((r) => r.id)).toEqual([
      "peer",
      "group",
      "def",
    ]);
  });

  it("breaks ties on priority then recency", () => {
    const rows = [
      binding({ id: "lo", match_scope: "peer", priority: 1, created_at: "2026-01-01T00:00:00Z" }),
      binding({ id: "hi", match_scope: "peer", priority: 9, created_at: "2026-01-01T00:00:00Z" }),
      binding({ id: "new", match_scope: "peer", priority: 1, created_at: "2026-02-01T00:00:00Z" }),
    ];
    expect(sortBindingsBySpecificity(rows).map((r) => r.id)).toEqual([
      "hi",
      "new",
      "lo",
    ]);
  });

  it("does not mutate the input array", () => {
    const rows = [
      binding({ id: "a", match_scope: "channel_default", match_value: null }),
      binding({ id: "b", match_scope: "peer" }),
    ];
    const before = rows.map((r) => r.id);
    sortBindingsBySpecificity(rows);
    expect(rows.map((r) => r.id)).toEqual(before);
  });
});

describe("scopeRefKind", () => {
  it("maps workspace + squad scopes to their ref kind, others to null", () => {
    expect(scopeRefKind("workspace")).toBe("workspace");
    expect(scopeRefKind("squad")).toBe("squad");
    expect(scopeRefKind("agent")).toBeNull();
    expect(scopeRefKind("user")).toBeNull();
  });
});

describe("parseKeywords / joinKeywords", () => {
  it("splits on commas and newlines, trims + lowercases + dedupes", () => {
    expect(parseKeywords("Expense, 报销\n  expense ")).toEqual([
      "expense",
      "报销",
    ]);
  });

  it("returns [] for blank input", () => {
    expect(parseKeywords("   ")).toEqual([]);
    expect(parseKeywords("")).toEqual([]);
  });

  it("round-trips through join", () => {
    expect(joinKeywords(["a", "b"])).toBe("a, b");
    expect(parseKeywords(joinKeywords(["x", "y"]))).toEqual(["x", "y"]);
  });
});
