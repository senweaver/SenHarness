"use client";

import { useState } from "react";
import { Link } from "@/lib/navigation";
import { IconBell, IconInbox, IconMailOpened } from "@tabler/icons-react";
import { useTranslations } from "next-intl";

import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from "@/components/ui/popover";
import {
  useApprovalsCount,
  useUrgentApprovals,
} from "@/hooks/use-approvals";
import {
  useMarkAllNotificationsRead,
  useNotifications,
  useUnreadNotificationCount,
} from "@/hooks/use-notifications";
import {
  resolveNotificationBody,
  resolveNotificationTitle,
} from "@/lib/notification-i18n";
import { cn } from "@/lib/utils";
import type { ApprovalRead, NotificationRead } from "@/types/api";

type Tab = "notifications" | "approvals";

export function NotificationBell({ className }: { className?: string }) {
  const t = useTranslations("notification");
  const tBell = useTranslations("notification.bell");
  const [open, setOpen] = useState(false);
  const [tab, setTab] = useState<Tab>("notifications");

  const { data: unreadCount } = useUnreadNotificationCount();
  const { data: approvalsCount } = useApprovalsCount();
  const unread = unreadCount?.unread ?? 0;
  const pending = approvalsCount?.pending ?? 0;
  const total = unread + pending;

  const notifications = useNotifications({ limit: 10, enabled: open && tab === "notifications" });
  const approvals = useUrgentApprovals({
    limit: 10,
    enabled: open && tab === "approvals",
  });
  const markAll = useMarkAllNotificationsRead();

  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger
        className={cn(
          "relative flex size-8 items-center justify-center rounded-md hover:bg-black/5 dark:hover:bg-white/10",
          className,
        )}
        aria-label={t("bellTooltip")}
      >
        <IconBell className="size-4" />
        {total > 0 && (
          <span
            aria-label={t("unreadBadge", { count: total })}
            className="absolute -top-0.5 -right-0.5 flex h-3.5 min-w-3.5 items-center justify-center rounded-full bg-red-500 px-1 text-[9px] font-bold text-white"
          >
            {total > 99 ? "99+" : total}
          </span>
        )}
      </PopoverTrigger>
      <PopoverContent align="end" side="top" className="w-80 p-2">
        <div className="mb-2 flex items-center gap-1 rounded-md border bg-black/5 p-0.5 dark:bg-white/5">
          <TabButton
            active={tab === "notifications"}
            label={tBell("tabNotifications")}
            badge={unread}
            onClick={() => setTab("notifications")}
          />
          <TabButton
            active={tab === "approvals"}
            label={tBell("tabApprovals")}
            badge={pending}
            onClick={() => setTab("approvals")}
          />
        </div>

        {tab === "notifications" && (
          <>
            <div className="mb-2 flex items-center justify-end px-1 text-xs">
              <button
                type="button"
                onClick={() => markAll.mutate()}
                disabled={markAll.isPending || unread === 0}
                className="flex items-center gap-1 text-[11px] text-[rgb(var(--color-primary))] hover:underline disabled:cursor-not-allowed disabled:opacity-40"
              >
                <IconMailOpened className="size-3" />
                {t("markAllRead")}
              </button>
            </div>
            {notifications.isFetching && !notifications.data ? (
              <p className="py-4 text-center text-[11px] sh-muted">
                {t("loading")}
              </p>
            ) : !notifications.data || notifications.data.length === 0 ? (
              <p className="py-4 text-center text-[11px] sh-muted">
                {t("noNotifications")}
              </p>
            ) : (
              <ul className="space-y-1">
                {notifications.data.map((row) => (
                  <BellRow
                    key={row.id}
                    row={row}
                    onClick={() => setOpen(false)}
                  />
                ))}
              </ul>
            )}
            <div className="mt-2 flex items-center justify-between border-t pt-2 text-[11px]">
              <Link
                href="/notifications"
                onClick={() => setOpen(false)}
                className="inline-flex items-center gap-1 text-[rgb(var(--color-primary))] hover:underline"
              >
                <IconInbox className="size-3" />
                {t("bell.viewAllLink")}
              </Link>
              <Link
                href="/settings/notifications"
                onClick={() => setOpen(false)}
                className="sh-muted hover:underline"
              >
                {t("bell.openPrefs")}
              </Link>
            </div>
          </>
        )}

        {tab === "approvals" && (
          <>
            {approvals.isFetching && !approvals.data ? (
              <p className="py-4 text-center text-[11px] sh-muted">
                {t("loading")}
              </p>
            ) : !approvals.data || approvals.data.length === 0 ? (
              <p className="py-4 text-center text-[11px] sh-muted">
                {tBell("emptyApprovals")}
              </p>
            ) : (
              <ul className="space-y-1">
                {approvals.data.map((row) => (
                  <ApprovalRow
                    key={row.id}
                    row={row}
                    onClick={() => setOpen(false)}
                  />
                ))}
              </ul>
            )}
            <div className="mt-2 flex items-center justify-between border-t pt-2 text-[11px]">
              <Link
                href="/approvals"
                onClick={() => setOpen(false)}
                className="inline-flex items-center gap-1 text-[rgb(var(--color-primary))] hover:underline"
              >
                <IconInbox className="size-3" />
                {tBell("openApprovals")}
              </Link>
            </div>
          </>
        )}
      </PopoverContent>
    </Popover>
  );
}

function TabButton({
  active,
  label,
  badge,
  onClick,
}: {
  active: boolean;
  label: string;
  badge: number;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={cn(
        "flex flex-1 items-center justify-center gap-1.5 rounded px-2 py-1 text-[11px] font-medium transition-colors",
        active
          ? "bg-white text-[rgb(var(--color-fg))] shadow-sm dark:bg-slate-800"
          : "sh-muted hover:text-[rgb(var(--color-fg))]",
      )}
    >
      <span>{label}</span>
      {badge > 0 && (
        <span className="inline-flex h-3.5 min-w-3.5 items-center justify-center rounded-full bg-red-500 px-1 text-[9px] font-bold text-white">
          {badge > 99 ? "99+" : badge}
        </span>
      )}
    </button>
  );
}

function BellRow({
  row,
  onClick,
}: {
  row: NotificationRead;
  onClick: () => void;
}) {
  const tNs = useTranslations();
  const title = resolveNotificationTitle(row, tNs);
  const body = resolveNotificationBody(row, tNs);
  const isUnread = row.read_at === null;
  return (
    <li>
      <Link
        href={(row.action_url ?? "/settings/notifications") as never}
        onClick={onClick}
        className={cn(
          "flex flex-col gap-0.5 rounded px-2 py-1.5 text-xs hover:bg-black/5 dark:hover:bg-white/5",
          isUnread && "bg-black/[0.02] dark:bg-white/[0.04]",
        )}
      >
        <div className="flex items-center gap-2">
          <span className="truncate font-medium">{title}</span>
          {isUnread && (
            <span className="ml-auto inline-block size-1.5 rounded-full bg-[rgb(var(--color-primary))]" />
          )}
        </div>
        {body && (
          <div className="line-clamp-2 text-[10.5px] sh-muted">{body}</div>
        )}
      </Link>
    </li>
  );
}

function ApprovalRow({
  row,
  onClick,
}: {
  row: ApprovalRead;
  onClick: () => void;
}) {
  return (
    <li>
      <Link
        href={`/approvals?focus=${row.id}` as never}
        onClick={onClick}
        className="flex flex-col gap-0.5 rounded px-2 py-1.5 text-xs hover:bg-black/5 dark:hover:bg-white/5"
      >
        <div className="flex items-center gap-2">
          <span className="truncate font-medium">{row.tool_name}</span>
        </div>
        {row.summary && (
          <div className="line-clamp-2 text-[10.5px] sh-muted">
            {row.summary}
          </div>
        )}
      </Link>
    </li>
  );
}
