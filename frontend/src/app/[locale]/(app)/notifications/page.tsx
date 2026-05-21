"use client";

import { useEffect, useMemo, useState } from "react";
import {
  IconBell,
  IconCircleCheck,
  IconInbox,
  IconRefresh,
  IconSearch,
} from "@tabler/icons-react";
import { useLocale, useTranslations } from "next-intl";
import { toast } from "sonner";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { PageHeader } from "@/components/ui/page-header";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";
import { Link } from "@/lib/navigation";
import { NotificationDetailDrawer } from "@/components/notifications/NotificationDetailDrawer";
import {
  useMarkAllNotificationsRead,
  useMarkNotificationRead,
  useNotificationPrefs,
  useNotifications,
  useUnreadNotificationCount,
} from "@/hooks/use-notifications";
import {
  resolveNotificationBody,
  resolveNotificationTitle,
} from "@/lib/notification-i18n";
import { cn, relativeTime } from "@/lib/utils";
import type { NotificationRead } from "@/types/api";

type StatusFilter = "all" | "unread" | "read";
type UrgencyFilter = "all" | "info" | "warn" | "critical";

const PAGE_SIZE = 50;

/**
 * Full notification inbox. The bell popover only shows the 10 most
 * recent rows; this page lets the user browse, filter, search and
 * dispose of every event raised in the active workspace. The hooks
 * underneath are the same ones the bell uses, so a "mark all read"
 * here drops the badge instantly without a refetch.
 */
export default function NotificationInboxPage() {
  const t = useTranslations("notification");
  const tInbox = useTranslations("notification.inbox");
  const tNs = useTranslations();
  const locale = useLocale();

  const [status, setStatusRaw] = useState<StatusFilter>("all");
  const [eventKey, setEventKeyRaw] = useState<string>("__all__");
  const [urgency, setUrgencyRaw] = useState<UrgencyFilter>("all");
  const [searchInput, setSearchInput] = useState("");
  const [debouncedSearch, setDebouncedSearch] = useState("");
  const [pageSize, setPageSize] = useState(PAGE_SIZE);
  const [selectedId, setSelectedId] = useState<string | null>(null);

  // Resetting `pageSize` whenever the filter set changes belongs in the
  // setters rather than an effect — debouncing the search input is the
  // only thing the timer here actually needs.
  useEffect(() => {
    const handle = window.setTimeout(() => {
      setDebouncedSearch(searchInput);
      setPageSize(PAGE_SIZE);
    }, 300);
    return () => window.clearTimeout(handle);
  }, [searchInput]);

  const setStatus = (next: StatusFilter) => {
    setStatusRaw(next);
    setPageSize(PAGE_SIZE);
  };
  const setEventKey = (next: string) => {
    setEventKeyRaw(next);
    setPageSize(PAGE_SIZE);
  };
  const setUrgency = (next: UrgencyFilter) => {
    setUrgencyRaw(next);
    setPageSize(PAGE_SIZE);
  };

  const { data: prefs } = useNotificationPrefs();
  const { data: unreadCount } = useUnreadNotificationCount();
  const list = useNotifications({
    unreadOnly: status === "unread",
    readOnly: status === "read",
    eventKey: eventKey === "__all__" ? null : eventKey,
    urgency: urgency === "all" ? null : urgency,
    q: debouncedSearch,
    limit: pageSize,
    refetchIntervalMs: 30_000,
  });

  const markRead = useMarkNotificationRead();
  const markAll = useMarkAllNotificationsRead();

  const eventOptions = useMemo(() => {
    const fromCatalog =
      prefs?.catalog?.map((d) => d.key).sort((a, b) => a.localeCompare(b)) ??
      [];
    return ["__all__", ...fromCatalog];
  }, [prefs]);

  const rows = list.data ?? [];
  const isInitialLoading = list.isLoading && !list.data;
  const canLoadMore = rows.length >= pageSize;

  const handleRefresh = () => {
    list.refetch();
    toast.success(tInbox("refreshedToast"));
  };

  const handleMarkAll = async () => {
    try {
      const res = await markAll.mutateAsync();
      toast.success(tInbox("markedAllToast", { n: res.marked ?? 0 }));
    } catch (err) {
      toast.error(String((err as Error).message ?? err));
    }
  };

  return (
    <div className="p-6">
      <PageHeader
        title={tInbox("pageTitle")}
        description={tInbox("pageDescription")}
        actions={
          <>
            <Button asChild size="sm" variant="ghost">
              <Link href="/settings/notifications">
                <IconBell className="size-4" />
                {tInbox("settingsLink")}
              </Link>
            </Button>
            <Button
              variant="outline"
              size="sm"
              onClick={handleRefresh}
              disabled={list.isFetching}
            >
              <IconRefresh
                className={cn("size-4", list.isFetching && "animate-spin")}
              />
              {tInbox("refreshButton")}
            </Button>
            <Button
              size="sm"
              onClick={handleMarkAll}
              disabled={markAll.isPending || (unreadCount?.unread ?? 0) === 0}
            >
              <IconCircleCheck className="size-4" />
              {tInbox("markAllReadButton")}
            </Button>
          </>
        }
      />

      <Card className="mb-3">
        <CardContent className="grid gap-3 py-3 sm:grid-cols-4">
          <div className="sm:col-span-1">
            <label className="text-[11px] sh-muted">
              {tInbox("filterStatusLabel")}
            </label>
            <Select
              value={status}
              onValueChange={(v) => setStatus(v as StatusFilter)}
            >
              <SelectTrigger>
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="all">{tInbox("filterAll")}</SelectItem>
                <SelectItem value="unread">{tInbox("filterUnread")}</SelectItem>
                <SelectItem value="read">{tInbox("filterRead")}</SelectItem>
              </SelectContent>
            </Select>
          </div>
          <div className="sm:col-span-1">
            <label className="text-[11px] sh-muted">
              {tInbox("filterUrgencyLabel")}
            </label>
            <Select
              value={urgency}
              onValueChange={(v) => setUrgency(v as UrgencyFilter)}
            >
              <SelectTrigger>
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="all">
                  {tInbox("filterUrgencyAll")}
                </SelectItem>
                <SelectItem value="info">{t("urgencyInfo")}</SelectItem>
                <SelectItem value="warn">{t("urgencyWarn")}</SelectItem>
                <SelectItem value="critical">{t("urgencyCritical")}</SelectItem>
              </SelectContent>
            </Select>
          </div>
          <div className="sm:col-span-1">
            <label className="text-[11px] sh-muted">
              {tInbox("filterEventLabel")}
            </label>
            <Select
              value={eventKey}
              onValueChange={(v) => setEventKey(v)}
            >
              <SelectTrigger>
                <SelectValue />
              </SelectTrigger>
              <SelectContent className="max-h-72">
                {eventOptions.map((key) =>
                  key === "__all__" ? (
                    <SelectItem key={key} value={key}>
                      {tInbox("filterByEventAll")}
                    </SelectItem>
                  ) : (
                    <SelectItem key={key} value={key}>
                      {readEventLabel(tNs, key)}
                    </SelectItem>
                  ),
                )}
              </SelectContent>
            </Select>
          </div>
          <div className="sm:col-span-1">
            <label className="text-[11px] sh-muted">
              {tInbox("searchPlaceholder")}
            </label>
            <div className="relative">
              <IconSearch className="pointer-events-none absolute left-2 top-1/2 size-3.5 -translate-y-1/2 sh-muted" />
              <Input
                value={searchInput}
                onChange={(e) => setSearchInput(e.target.value)}
                placeholder={tInbox("searchPlaceholder")}
                className="pl-7"
              />
            </div>
          </div>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle className="flex items-center justify-between text-sm">
            <span className="flex items-center gap-2">
              <IconInbox className="size-4 sh-muted" />
              {tInbox("countLabel", { n: rows.length })}
            </span>
            <span className="text-[10px] font-normal sh-muted">
              {tInbox("autoRefreshHint")}
            </span>
          </CardTitle>
        </CardHeader>
        <CardContent>
          {isInitialLoading ? (
            <div className="space-y-2">
              <Skeleton className="h-12 w-full" />
              <Skeleton className="h-12 w-full" />
              <Skeleton className="h-12 w-full" />
            </div>
          ) : rows.length === 0 ? (
            <p className="py-12 text-center text-xs sh-muted">
              {tInbox("noNotifications")}
            </p>
          ) : (
            <ul className="divide-y">
              {rows.map((row) => (
                <NotificationRow
                  key={row.id}
                  row={row}
                  locale={locale}
                  onOpen={() => setSelectedId(row.id)}
                  onMarkRead={() => markRead.mutate({ id: row.id })}
                  isMarking={markRead.isPending && markRead.variables?.id === row.id}
                />
              ))}
            </ul>
          )}
        </CardContent>
        {rows.length > 0 && (
          <div className="flex items-center justify-center border-t p-3">
            {canLoadMore ? (
              <Button
                variant="outline"
                size="sm"
                onClick={() => setPageSize((n) => Math.min(n + PAGE_SIZE, 200))}
                disabled={list.isFetching || pageSize >= 200}
              >
                {list.isFetching
                  ? tInbox("loadingMore")
                  : tInbox("loadMoreButton")}
              </Button>
            ) : (
              <span className="text-[11px] sh-muted">
                {tInbox("endOfList")}
              </span>
            )}
          </div>
        )}
      </Card>

      <NotificationDetailDrawer
        notificationId={selectedId}
        open={selectedId !== null}
        onOpenChange={(next) => {
          if (!next) setSelectedId(null);
        }}
      />
    </div>
  );
}

interface RowProps {
  row: NotificationRead;
  locale: string;
  onOpen: () => void;
  onMarkRead: () => void;
  isMarking: boolean;
}

function NotificationRow({
  row,
  locale,
  onOpen,
  onMarkRead,
  isMarking,
}: RowProps) {
  const t = useTranslations("notification");
  const tInbox = useTranslations("notification.inbox");
  const tNs = useTranslations();
  const title = resolveNotificationTitle(row, tNs);
  const body = resolveNotificationBody(row, tNs);
  const isUnread = row.read_at === null;
  const urgency =
    (row.metadata_json as Record<string, unknown> | undefined)?.urgency ??
    row.level ??
    "info";

  return (
    <li
      className={cn(
        "group flex items-stretch gap-3 px-1 py-3 text-sm transition-colors",
        isUnread && "bg-black/[0.02] dark:bg-white/[0.04]",
      )}
    >
      <UrgencyBar urgency={String(urgency)} />
      <button
        type="button"
        onClick={onOpen}
        aria-label={tInbox("rowOpenAria", { title })}
        className="min-w-0 flex-1 cursor-pointer text-left"
      >
        <div className="flex flex-wrap items-center gap-2">
          <span
            className={cn(
              "truncate text-sm",
              isUnread ? "font-semibold" : "font-medium sh-muted",
            )}
          >
            {title}
          </span>
          {isUnread ? (
            <Badge variant="primary">{tInbox("rowUnreadBadge")}</Badge>
          ) : (
            <Badge variant="outline">{tInbox("rowReadBadge")}</Badge>
          )}
          <span className="ml-auto whitespace-nowrap text-[11px] sh-muted">
            {relativeTime(row.created_at, locale)}
          </span>
        </div>
        {body && (
          <p className="mt-1 line-clamp-2 text-[12px] sh-muted">{body}</p>
        )}
        <p className="mt-1 truncate text-[10px] font-mono sh-muted">
          {row.kind}
        </p>
      </button>
      <div className="flex shrink-0 items-center">
        {isUnread ? (
          <Button
            variant="ghost"
            size="sm"
            disabled={isMarking}
            onClick={(e) => {
              e.stopPropagation();
              onMarkRead();
            }}
            title={t("markAllRead")}
          >
            <IconCircleCheck className="size-4" />
          </Button>
        ) : null}
      </div>
    </li>
  );
}

function UrgencyBar({ urgency }: { urgency: string }) {
  const u = urgency.toLowerCase();
  const colour =
    u === "critical" || u === "error"
      ? "bg-red-500"
      : u === "warn" || u === "warning"
        ? "bg-amber-500"
        : "bg-sky-500";
  return <span className={cn("w-0.5 shrink-0 rounded-full", colour)} />;
}

type IntlNamespaceTranslator = (key: string) => string;

function readEventLabel(
  tNs: IntlNamespaceTranslator,
  key: string,
): string {
  const i18nKey = eventTitleI18nKey(key);
  if (!i18nKey) return key;
  try {
    const value = tNs(i18nKey);
    if (typeof value === "string" && value !== i18nKey) return value;
  } catch {
    // missing translations fall back to the raw event key
  }
  return key;
}

function eventTitleI18nKey(key: string): string | null {
  switch (key) {
    case "goal.alignment_low":
      return "notification.goalAlignmentLow.title";
    case "goal.locked":
      return "notification.goalLocked.title";
    case "goal.unlocked":
      return "notification.goalUnlocked.title";
    case "judge.score_negative":
      return "notification.judgeScoreNegative.title";
    case "judge.degraded":
      return "notification.judgeDegraded.title";
    case "channel.sender_blocked":
      return "notification.channelSenderBlocked.title";
    case "security.signature_failed":
      return "notification.securitySignatureFailed.title";
    case "auth.workspace_provisioned":
      return "notification.workspaceProvisioned.title";
    case "workspace.quota_exceeded":
      return "notification.quotaExceeded.title";
    case "workspace.spike_detected":
      return "notification.spikeDetected.title";
    case "workspace.quota_increased":
      return "notification.quotaIncreased.title";
    case "job.failed_permanent":
      return "notification.jobFailedPermanent.title";
    case "approval.expiring":
      return "notification.approvalExpiring.title";
    case "platform_settings.changed":
      return "notification.platformSettingsChanged.title";
    case "subagent.zombie_detected":
      return "notification.subagentZombieDetected.title";
    case "provider.cooldown_admin_alert":
      return "notification.providerCooldownStarted.title";
    case "inflight_run.lost_detected":
      return "notification.inflightRunLostDetected.title";
    case "inflight_run.force_recycled":
      return "notification.inflightRunForceRecycled.title";
    case "cache.adaptive_disabled":
      return "notification.cacheAdaptiveDisabled.title";
    default:
      return null;
  }
}
