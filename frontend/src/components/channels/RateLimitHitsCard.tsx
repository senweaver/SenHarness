"use client";

import { useTranslations } from "next-intl";

import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { useChannelRateLimitHits } from "@/hooks/use-channel-audit";
import { relativeTime } from "@/lib/utils";

interface RateLimitHitsCardProps {
  channelId: string;
}

export function RateLimitHitsCard({ channelId }: RateLimitHitsCardProps) {
  const t = useTranslations("channelSecurity");
  const { data, isLoading } = useChannelRateLimitHits(channelId);

  if (isLoading) {
    return null;
  }
  if (!data || data.length === 0) {
    return null;
  }

  return (
    <Card>
      <CardHeader className="pb-2">
        <CardTitle className="text-sm">{t("rateLimitHitsTitle")}</CardTitle>
      </CardHeader>
      <CardContent>
        <ul className="grid gap-1.5 text-[11px]">
          {data.map((evt) => {
            const meta = evt.metadata_json ?? {};
            const limitKind = String(
              (meta as { limit_kind?: string }).limit_kind ?? "",
            );
            const senderId = String(
              (meta as { sender_id?: string }).sender_id ?? "",
            );
            return (
              <li
                key={evt.id}
                className="flex items-center justify-between gap-2 rounded-md border px-2 py-1.5"
              >
                <div className="min-w-0 flex-1">
                  <div className="flex items-center gap-1">
                    <span className="font-mono text-[10px] uppercase tracking-wide sh-muted">
                      {limitKind || "unknown"}
                    </span>
                    {senderId && (
                      <span className="truncate font-mono">{senderId}</span>
                    )}
                  </div>
                  {evt.summary && (
                    <p className="truncate sh-muted">{evt.summary}</p>
                  )}
                </div>
                <span className="shrink-0 sh-muted">
                  {relativeTime(evt.created_at)}
                </span>
              </li>
            );
          })}
        </ul>
      </CardContent>
    </Card>
  );
}
