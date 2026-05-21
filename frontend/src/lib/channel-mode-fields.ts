/**
 * Helpers for resolving the *active* config-field set on a channel.
 *
 * The backend sends two layers:
 *
 *   - Global ``required_config_fields`` / ``optional_config_fields``
 *     — every field the provider knows about, irrespective of mode.
 *   - Per-mode ``mode_required_fields[mode]`` / ``mode_optional_fields[mode]``
 *     — only what's actually needed for the active transport.
 *
 * The form renders the per-mode set when present and falls back to the
 * global one for community providers that don't bother with the split.
 *
 * We also expose a "webhook-only fields" helper: fields that show up in
 * webhook mode but not in stream mode, so the AdvancedSettings panel
 * can surface them only when the operator opens it.
 */

import type { ChannelKindMeta, ChannelMode } from "@/hooks/use-channels";

export function pickRequiredFields(
  meta: ChannelKindMeta,
  mode: ChannelMode,
): string[] {
  const override = meta.mode_required_fields?.[mode];
  if (override) return [...override];
  return [...meta.required_config_fields];
}

export function pickOptionalFields(
  meta: ChannelKindMeta,
  mode: ChannelMode,
): string[] {
  const override = meta.mode_optional_fields?.[mode];
  if (override) return [...override];
  return [...meta.optional_config_fields];
}

export function pickHiddenFields(
  meta: ChannelKindMeta,
  mode: ChannelMode,
): string[] {
  return meta.mode_hidden_fields?.[mode] ?? [];
}

/**
 * Fields the operator only needs in webhook mode (the diff between
 * webhook's required+optional and stream's required+optional). Used by
 * AdvancedSettings to render the extra inputs that appear when the
 * operator flips the toggle to "公网回调".
 *
 * Returns ``[]`` for providers that don't support webhook mode.
 */
export function pickWebhookOnlyFields(meta: ChannelKindMeta): string[] {
  if (!meta.supported_modes?.includes("webhook")) return [];
  const streamFields = new Set([
    ...pickRequiredFields(meta, "stream"),
    ...pickOptionalFields(meta, "stream"),
  ]);
  const webhookFields = [
    ...pickRequiredFields(meta, "webhook"),
    ...pickOptionalFields(meta, "webhook"),
  ];
  const seen = new Set<string>();
  const out: string[] = [];
  for (const f of webhookFields) {
    if (streamFields.has(f) || seen.has(f)) continue;
    seen.add(f);
    out.push(f);
  }
  return out;
}

export function pickWebhookRequiredFields(meta: ChannelKindMeta): string[] {
  if (!meta.supported_modes?.includes("webhook")) return [];
  return pickRequiredFields(meta, "webhook");
}

export function isDualMode(meta: ChannelKindMeta): boolean {
  return (meta.supported_modes?.length ?? 0) > 1;
}

export function defaultMode(meta: ChannelKindMeta): ChannelMode {
  return meta.default_mode ?? meta.supported_modes?.[0] ?? "webhook";
}
