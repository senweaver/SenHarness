import type { useTranslations } from "next-intl";

import type { NotificationRead } from "@/types/api";

export type NamespaceTranslator = ReturnType<typeof useTranslations>;

interface NotificationMetadata {
  title_key?: string;
  message_key?: string;
  payload?: Record<string, unknown>;
}

export function resolveNotificationTitle(
  row: NotificationRead,
  tNs: NamespaceTranslator,
): string {
  return resolveNotificationField(row, tNs, "title");
}

export function resolveNotificationBody(
  row: NotificationRead,
  tNs: NamespaceTranslator,
): string | null {
  const body = resolveNotificationField(row, tNs, "body");
  return body ? body : null;
}

function resolveNotificationField(
  row: NotificationRead,
  tNs: NamespaceTranslator,
  field: "title" | "body",
): string {
  const stored = field === "title" ? row.title : (row.body ?? "");
  const meta = row.metadata_json as NotificationMetadata;
  const key =
    field === "title"
      ? meta.title_key ?? i18nKeyFromStored(stored)
      : meta.message_key ?? i18nKeyFromStored(stored);
  if (!key) return stored;

  try {
    const values = intlValuesFromPayload(meta.payload);
    const translated = tNs(key, values);
    if (typeof translated === "string" && translated !== key) {
      return translated;
    }
  } catch {
    // missing keys or format errors — show stored fallback
  }
  return stored;
}

function i18nKeyFromStored(value: string): string | undefined {
  return value.startsWith("notification.") ? value : undefined;
}

function intlValuesFromPayload(
  payload: Record<string, unknown> | undefined,
): Record<string, string | number> {
  if (!payload || typeof payload !== "object") return {};
  const values: Record<string, string | number> = {};
  for (const [name, raw] of Object.entries(payload)) {
    if (raw === null || raw === undefined) continue;
    values[name] = typeof raw === "number" ? raw : String(raw);
  }
  return values;
}
