/**
 * Pure helpers for the channel routing UI (P1). Kept framework-free so
 * the routing-fields form and the layered-binding editor share one
 * source of truth and the logic is unit-testable without React.
 *
 * Mirrors the backend: binding specificity ranking matches
 * ``_BINDING_SPECIFICITY`` in ``app/services/channel_routing.py`` and the
 * keyword parsing matches ``_handoff.parse_handoff_rules``.
 */
import type {
  BindScope,
  BindingMatchScope,
  ChannelBinding,
} from "@/hooks/use-channels";

/**
 * Whether a bind scope points ``scope_ref_id`` at another object the
 * operator must pick: a workspace (``workspace``) or a squad (``squad``).
 * ``agent`` / ``user`` resolve their pool implicitly and carry no ref.
 */
export function scopeRefKind(scope: BindScope): "workspace" | "squad" | null {
  if (scope === "workspace") return "workspace";
  if (scope === "squad") return "squad";
  return null;
}

/**
 * Specificity rung per match scope — higher wins. Only ``peer`` /
 * ``group`` / ``channel_default`` are matched at dispatch today; the
 * rest are reserved rungs of the ladder kept in sync with the backend.
 */
export const BINDING_SPECIFICITY: Record<BindingMatchScope, number> = {
  peer: 70,
  thread: 60,
  role: 50,
  guild: 40,
  team: 30,
  account: 20,
  group: 10,
  channel_default: 0,
};

/** Scopes that don't carry a match value (the fallback rung). */
export function requiresMatchValue(scope: BindingMatchScope): boolean {
  return scope !== "channel_default";
}

/**
 * Order bindings the way the resolver ranks them: most-specific first,
 * then higher ``priority``, then newest. Pure + stable so the editor can
 * show rules in the order they'd actually win.
 */
export function sortBindingsBySpecificity(
  rows: readonly ChannelBinding[],
): ChannelBinding[] {
  return [...rows].sort((a, b) => {
    const sa = BINDING_SPECIFICITY[a.match_scope] ?? -1;
    const sb = BINDING_SPECIFICITY[b.match_scope] ?? -1;
    if (sa !== sb) return sb - sa;
    if (a.priority !== b.priority) return b.priority - a.priority;
    return (b.created_at ?? "").localeCompare(a.created_at ?? "");
  });
}

/**
 * Split a free-text keyword field (comma / newline separated) into a
 * de-duplicated, trimmed, lower-cased keyword list. Matches the
 * case-insensitive substring matching the backend handoff router does.
 */
export function parseKeywords(raw: string): string[] {
  const seen = new Set<string>();
  const out: string[] = [];
  for (const part of (raw ?? "").split(/[\n,]/)) {
    const kw = part.trim().toLowerCase();
    if (kw && !seen.has(kw)) {
      seen.add(kw);
      out.push(kw);
    }
  }
  return out;
}

/** Render a keyword list back into the editable comma-joined string. */
export function joinKeywords(keywords: readonly string[]): string {
  return (keywords ?? []).join(", ");
}
