/**
 * Map a notification onto a destination URL inside the app shell.
 *
 * Resolution order:
 *   1. `notification.action_url` if the dispatcher set one — the
 *      backend always wins because it has the freshest information
 *      about the trigger (e.g. a goal alignment fan-out can pin to
 *      a specific message id, not just the session).
 *   2. A category-driven fallback computed from `event_key` and the
 *      payload bag stored under `metadata_json.payload`.
 *
 * The mapping is intentionally conservative: when the payload doesn't
 * carry the field needed to deep-link (e.g. a security signature
 * failure with no channel id), the resolver returns the parent admin
 * page rather than fabricating a URL.
 */
import type { NotificationRead } from "@/types/api";

export type NotificationSourceKind =
  | "alignment"
  | "judgeTrace"
  | "channelAdmin"
  | "workspaceQuota"
  | "auditLog"
  | "providerCatalog"
  | "runtime"
  | "approvalQueue"
  | "settings";

export interface NotificationSourceTarget {
  href: string;
  /** i18n key under `notification.openSource.<kind>` for the link label. */
  kind: NotificationSourceKind;
  payloadVars: Record<string, string>;
}

function readPayload(row: NotificationRead): Record<string, unknown> {
  const meta = row.metadata_json as Record<string, unknown> | undefined;
  const payload = meta?.payload;
  if (payload && typeof payload === "object") return payload as Record<string, unknown>;
  return {};
}

function pickString(
  bag: Record<string, unknown>,
  key: string,
): string | null {
  const value = bag[key];
  if (typeof value === "string" && value) return value;
  return null;
}

export function resolveNotificationSource(
  row: NotificationRead,
): NotificationSourceTarget | null {
  if (row.action_url) {
    return {
      href: row.action_url,
      kind: "settings",
      payloadVars: {},
    };
  }

  const payload = readPayload(row);
  const sessionId = pickString(payload, "session_id");
  const runId = pickString(payload, "run_id");
  const channelId = pickString(payload, "channel_id");
  const providerKind = pickString(payload, "provider_kind");

  switch (row.kind) {
    case "goal.alignment_low":
    case "goal.locked":
    case "goal.unlocked":
      if (sessionId) {
        return {
          href: `/chat/${sessionId}`,
          kind: "alignment",
          payloadVars: { session: sessionId },
        };
      }
      return null;

    case "judge.score_negative":
      if (sessionId) {
        return {
          href: `/traces/${sessionId}`,
          kind: "judgeTrace",
          payloadVars: { run: runId ?? sessionId },
        };
      }
      return null;

    case "judge.degraded":
      return {
        href: "/settings/workspace/providers",
        kind: "providerCatalog",
        payloadVars: providerKind ? { provider: providerKind } : {},
      };

    case "channel.sender_blocked":
      return {
        href: channelId ? `/channels?channel=${channelId}` : "/channels",
        kind: "channelAdmin",
        payloadVars: {},
      };

    case "security.signature_failed":
      return {
        href: "/settings/audit",
        kind: "auditLog",
        payloadVars: {},
      };

    case "workspace.quota_exceeded":
    case "workspace.spike_detected":
    case "workspace.quota_increased":
      return {
        href: "/settings/workspace/quota",
        kind: "workspaceQuota",
        payloadVars: {},
      };

    case "auth.workspace_provisioned":
      return {
        href: "/settings/profile",
        kind: "settings",
        payloadVars: {},
      };

    case "job.failed_permanent":
      return {
        href: "/settings/system/jobs",
        kind: "settings",
        payloadVars: {},
      };

    case "approval.expiring":
      return {
        href: "/approvals",
        kind: "approvalQueue",
        payloadVars: {},
      };

    case "subagent.zombie_detected":
    case "inflight_run.force_recycled":
      return {
        href: "/settings/system/runtime",
        kind: "runtime",
        payloadVars: {},
      };

    case "inflight_run.lost_detected":
      if (sessionId) {
        return {
          href: `/chat/${sessionId}`,
          kind: "alignment",
          payloadVars: { session: sessionId },
        };
      }
      return {
        href: "/settings/system/runtime",
        kind: "runtime",
        payloadVars: {},
      };

    case "provider.cooldown_admin_alert":
    case "cache.adaptive_disabled":
      return {
        href: "/settings/workspace/providers",
        kind: "providerCatalog",
        payloadVars: providerKind ? { provider: providerKind } : {},
      };

    case "platform_settings.changed":
      return {
        href: "/admin",
        kind: "settings",
        payloadVars: {},
      };

    default:
      return null;
  }
}

/**
 * Extract the `payload` field of a notification row as a flat
 * `key → value` table for the detail drawer.
 */
export function flattenNotificationPayload(
  row: NotificationRead,
): Array<[string, string]> {
  const payload = readPayload(row);
  const out: Array<[string, string]> = [];
  for (const [key, value] of Object.entries(payload)) {
    if (value === null || value === undefined) continue;
    if (typeof value === "string" || typeof value === "number" || typeof value === "boolean") {
      out.push([key, String(value)]);
      continue;
    }
    try {
      out.push([key, JSON.stringify(value)]);
    } catch {
      out.push([key, String(value)]);
    }
  }
  return out;
}
