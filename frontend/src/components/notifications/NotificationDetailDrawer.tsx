"use client";

import { useMemo } from "react";
import { Link } from "@/lib/navigation";
import {
  IconArrowRight,
  IconCircleCheck,
  IconCircleDot,
} from "@tabler/icons-react";
import { useLocale, useTranslations } from "next-intl";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetHeader,
  SheetTitle,
} from "@/components/ui/sheet";
import {
  useMarkNotificationRead,
  useMarkNotificationUnread,
  useNotification,
} from "@/hooks/use-notifications";
import {
  resolveNotificationBody,
  resolveNotificationTitle,
} from "@/lib/notification-i18n";
import {
  flattenNotificationPayload,
  resolveNotificationSource,
} from "@/lib/notification-source";
import { cn } from "@/lib/utils";

interface NotificationDetailDrawerProps {
  notificationId: string | null;
  open: boolean;
  onOpenChange: (next: boolean) => void;
}

export function NotificationDetailDrawer({
  notificationId,
  open,
  onOpenChange,
}: NotificationDetailDrawerProps) {
  const t = useTranslations("notification");
  const tDetail = useTranslations("notification.detail");
  const tSource = useTranslations("notification.openSource");
  const tNs = useTranslations();
  const locale = useLocale();
  const { data, isLoading } = useNotification(notificationId);
  const markRead = useMarkNotificationRead();
  const markUnread = useMarkNotificationUnread();

  const source = useMemo(
    () => (data ? resolveNotificationSource(data) : null),
    [data],
  );
  const payloadRows = useMemo(
    () => (data ? flattenNotificationPayload(data) : []),
    [data],
  );

  const title = data ? resolveNotificationTitle(data, tNs) : "";
  const body = data ? resolveNotificationBody(data, tNs) : null;
  const isUnread = data?.read_at === null;
  const urgency =
    (data?.metadata_json as Record<string, unknown> | undefined)?.urgency ??
    data?.level ??
    "info";

  return (
    <Sheet open={open} onOpenChange={onOpenChange}>
      <SheetContent className="flex flex-col">
        <SheetHeader>
          <SheetTitle>{tDetail("title")}</SheetTitle>
          <SheetDescription>{tDetail("description")}</SheetDescription>
        </SheetHeader>

        {isLoading || !data ? (
          <div className="space-y-3">
            <Skeleton className="h-6 w-3/4" />
            <Skeleton className="h-4 w-full" />
            <Skeleton className="h-32 w-full" />
          </div>
        ) : (
          <div className="flex-1 space-y-4 overflow-y-auto pr-1">
            <div>
              <div className="flex flex-wrap items-center gap-2">
                <h3 className="text-sm font-semibold">{title}</h3>
                <UrgencyBadge urgency={String(urgency)} />
                {isUnread && (
                  <Badge variant="primary">{tDetail("metaUnread")}</Badge>
                )}
              </div>
              {body && (
                <p className="mt-2 whitespace-pre-wrap text-xs text-[rgb(var(--color-fg))]/85">
                  {body}
                </p>
              )}
            </div>

            <div className="grid grid-cols-[100px_1fr] gap-x-3 gap-y-1 rounded-md border p-3 text-[11px]">
              <span className="sh-muted">{tDetail("metaEvent")}</span>
              <span className="font-mono break-all">{data.kind}</span>
              <span className="sh-muted">{tDetail("metaUrgency")}</span>
              <span>{String(urgency)}</span>
              <span className="sh-muted">{tDetail("metaCreatedAt")}</span>
              <span>
                {new Date(data.created_at).toLocaleString(locale)}
              </span>
              {data.read_at && (
                <>
                  <span className="sh-muted">{tDetail("metaReadAt")}</span>
                  <span>{new Date(data.read_at).toLocaleString(locale)}</span>
                </>
              )}
              {data.actor_identity_id && (
                <>
                  <span className="sh-muted">{tDetail("metaActor")}</span>
                  <span className="font-mono break-all">
                    {data.actor_identity_id}
                  </span>
                </>
              )}
            </div>

            <div>
              <p className="mb-1 text-[11px] font-medium sh-muted">
                {tDetail("payloadTitle")}
              </p>
              {payloadRows.length === 0 ? (
                <p className="rounded-md border border-dashed p-2 text-[11px] sh-muted">
                  {tDetail("metadataMissing")}
                </p>
              ) : (
                <div className="grid grid-cols-[120px_1fr] gap-x-3 gap-y-1 rounded-md border p-3 text-[11px]">
                  {payloadRows.map(([k, v]) => (
                    <span key={k} className="contents">
                      <span className="sh-muted break-all">{k}</span>
                      <span className="font-mono break-all">{v}</span>
                    </span>
                  ))}
                </div>
              )}
            </div>
          </div>
        )}

        <div className="mt-auto space-y-2 border-t pt-3">
          {source ? (
            <Button asChild className="w-full" size="sm">
              <Link
                href={source.href as never}
                onClick={() => onOpenChange(false)}
              >
                <IconArrowRight className="size-4" />
                {tDetail("openInSourceButton")}
                <span className="ml-1 text-[10px] opacity-70">
                  {tSource(source.kind, source.payloadVars)}
                </span>
              </Link>
            </Button>
          ) : (
            <p className="rounded-md bg-black/[0.03] p-2 text-center text-[11px] sh-muted dark:bg-white/[0.04]">
              {tDetail("openInSourceUnavailable")}
            </p>
          )}
          <div className="flex items-center justify-between gap-2">
            <Button
              variant="outline"
              size="sm"
              disabled={!data || markRead.isPending || markUnread.isPending}
              onClick={() => {
                if (!data) return;
                if (isUnread) {
                  markRead.mutate({ id: data.id });
                } else {
                  markUnread.mutate({ id: data.id });
                }
              }}
            >
              {isUnread ? (
                <>
                  <IconCircleCheck className="size-4" />
                  {tDetail("markReadButton")}
                </>
              ) : (
                <>
                  <IconCircleDot className="size-4" />
                  {tDetail("markUnreadButton")}
                </>
              )}
            </Button>
            <Button
              variant="ghost"
              size="sm"
              onClick={() => onOpenChange(false)}
            >
              {tDetail("closeButton")}
            </Button>
          </div>
        </div>
        <span className="sr-only">{t("bellTooltip")}</span>
      </SheetContent>
    </Sheet>
  );
}

function UrgencyBadge({ urgency }: { urgency: string }) {
  const t = useTranslations("notification");
  const u = urgency.toLowerCase();
  if (u === "critical" || u === "error") {
    return <Badge variant="destructive">{t("urgencyCritical")}</Badge>;
  }
  if (u === "warn" || u === "warning") {
    return <Badge variant="warning">{t("urgencyWarn")}</Badge>;
  }
  return (
    <Badge variant="outline" className={cn(u === "success" && "text-green-600")}>
      {t("urgencyInfo")}
    </Badge>
  );
}
