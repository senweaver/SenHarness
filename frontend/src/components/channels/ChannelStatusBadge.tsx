"use client";

import { useTranslations } from "next-intl";

import { useChannelStatus, type ChannelKind } from "@/hooks/use-channels";
import { cn, relativeTime } from "@/lib/utils";

interface ChannelStatusBadgeProps {
  channelId: string;
  kind: ChannelKind;
  /** Snapshot of ``config_json`` from the channel row (already masked
   *  by the backend). Used to detect "wechat without bot_token =
   *  waiting for QR scan" without an extra round-trip. */
  config: Record<string, unknown>;
  className?: string;
}

type Tone = "connected" | "idleWebhook" | "waitingScan" | "disconnected";

const TONE_DOT: Record<Tone, string> = {
  connected: "bg-[#1AAD19]",
  idleWebhook: "bg-[rgb(var(--color-muted-foreground))]",
  waitingScan: "bg-[#F59E0B]",
  disconnected: "bg-[#EF4444]",
};

/**
 * Single colored dot + short label that summarises the channel's
 * runtime state for non-technical operators. Polls
 * ``GET /channels/{id}/status`` every 5s under the hood.
 *
 * Tone selection (in order — first match wins):
 *
 * 1. ``connected`` — runtime says ``connected=true``
 * 2. ``waitingScan`` — kind is wechat AND no ``bot_token`` in config
 * 3. ``idleWebhook`` — webhook mode with no ``last_event_at`` yet
 * 4. ``disconnected`` — anything else (with optional ``last_error``
 *    surfaced via the native ``title`` tooltip).
 */
export function ChannelStatusBadge({
  channelId,
  kind,
  config,
  className,
}: ChannelStatusBadgeProps) {
  const t = useTranslations("settings.channels.status");
  const { data } = useChannelStatus(channelId);

  const hasToken = Boolean(
    typeof config?.bot_token === "string" && (config.bot_token as string).length > 0,
  );

  let tone: Tone = "disconnected";
  let label = t("neverConnected");
  let title: string | undefined;

  if (data?.connected) {
    tone = "connected";
    label = t("connected");
    if (data.last_event_at) {
      title = t("lastEvent", { when: relativeTime(data.last_event_at) });
    }
  } else if (kind === "wechat" && !hasToken) {
    tone = "waitingScan";
    label = t("waitingScan");
  } else if (data?.mode === "webhook" && !data.last_event_at) {
    tone = "idleWebhook";
    label = t("idleWebhook");
  } else if (data) {
    tone = "disconnected";
    label = t("disconnected");
    if (data.last_error) {
      title = t("lastError", { error: data.last_error });
    }
  }

  return (
    <span
      className={cn(
        "inline-flex items-center gap-1.5 rounded-full px-2 py-0.5 text-[11px]",
        "border bg-[rgb(var(--color-card))]",
        className,
      )}
      title={title}
      data-testid="channel-status-badge"
      data-tone={tone}
    >
      <span
        className={cn(
          "size-1.5 rounded-full",
          TONE_DOT[tone],
          tone === "connected" && "animate-pulse",
        )}
        aria-hidden
      />
      {label}
    </span>
  );
}

export default ChannelStatusBadge;
