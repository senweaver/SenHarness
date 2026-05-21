"use client";

import { useState } from "react";
import { Link } from "@/lib/navigation";
import {
  IconDots,
  IconInfoCircle,
  IconMessageCircle2,
  IconPin,
  IconPinnedOff,
  IconRobot,
  IconUsersGroup,
  IconX,
} from "@tabler/icons-react";
import { useTranslations } from "next-intl";

import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from "@/components/ui/popover";
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip";
import {
  useTogglePin,
  useUnstarItem,
} from "@/hooks/use-sidebar-items";
import type { SidebarItem } from "@/types/api";
import { cn } from "@/lib/utils";

interface MyItemRowProps {
  item: SidebarItem;
  collapsed: boolean;
  active: boolean;
}

function detailHref(item: SidebarItem): string {
  switch (item.type) {
    case "agent":
      return `/agents/${item.id}`;
    case "squad":
      return `/squads/${item.id}`;
    case "session":
      return `/sessions/${item.id}`;
  }
}

export function chatHref(item: SidebarItem): string {
  switch (item.type) {
    case "agent":
      return `/chat/new?agent=${item.id}`;
    case "squad":
      return `/chat/new?squad=${item.id}`;
    case "session":
      return `/chat/${item.id}`;
  }
}

function ItemAvatar({ item }: { item: SidebarItem }) {
  const seed = item.avatar_seed?.slice(0, 1).toUpperCase() || "?";
  if (item.type === "session") {
    return (
      <span className="flex size-6 shrink-0 items-center justify-center rounded-full bg-black/5 text-[10px] font-semibold dark:bg-white/10">
        <IconMessageCircle2 className="size-3.5 sh-muted" />
      </span>
    );
  }
  if (item.type === "squad") {
    return (
      <span className="relative flex size-6 shrink-0 items-center justify-center">
        <span className="absolute -left-0.5 top-0.5 flex size-4 items-center justify-center rounded-sm bg-black/10 text-[9px] font-semibold dark:bg-white/15">
          <IconUsersGroup className="size-3" />
        </span>
        <span className="absolute -right-0.5 bottom-0.5 flex size-4 items-center justify-center rounded-sm bg-[rgb(var(--color-primary)/0.18)] text-[9px] font-semibold text-[rgb(var(--color-primary))]">
          {seed}
        </span>
      </span>
    );
  }
  return (
    <span className="flex size-6 shrink-0 items-center justify-center rounded-md bg-[rgb(var(--color-primary)/0.12)] text-[10px] font-semibold text-[rgb(var(--color-primary))]">
      {seed}
    </span>
  );
}

function formatActivity(value: string | null, locale: string): string | null {
  if (!value) return null;
  try {
    const date = new Date(value);
    const now = new Date();
    const sameDay =
      date.getFullYear() === now.getFullYear() &&
      date.getMonth() === now.getMonth() &&
      date.getDate() === now.getDate();
    if (sameDay) {
      return new Intl.DateTimeFormat(locale, {
        hour: "2-digit",
        minute: "2-digit",
      }).format(date);
    }
    return new Intl.DateTimeFormat(locale, {
      month: "short",
      day: "numeric",
    }).format(date);
  } catch {
    return null;
  }
}

export function MyItemRow({ item, collapsed, active }: MyItemRowProps) {
  const t = useTranslations("sidebar.myItem");
  const togglePin = useTogglePin();
  const unstar = useUnstarItem();
  const [menuOpen, setMenuOpen] = useState(false);
  const activityLabel = formatActivity(item.last_activity_at, "en-US");
  const seed = item.avatar_seed?.slice(0, 1).toUpperCase() || "?";

  const row = (
    <Link
      href={chatHref(item)}
      aria-current={active ? "page" : undefined}
      className={cn(
        "sh-nav-item group relative flex items-center rounded-md text-[13px]",
        active ? "sh-nav-active" : "sh-menu-text",
        collapsed
          ? "size-9 mx-auto justify-center px-0"
          : "h-9 gap-2 px-2",
      )}
    >
      <ItemAvatar item={item} />
      {!collapsed && (
        <>
          <span className="min-w-0 flex-1 truncate">{item.name}</span>
          {item.unread_count > 0 && (
            <span className="inline-flex h-4 min-w-[16px] items-center justify-center rounded-full bg-red-500 px-1 text-[10px] font-bold text-white">
              {item.unread_count > 99 ? "99+" : item.unread_count}
            </span>
          )}
          {activityLabel && item.unread_count === 0 && (
            <span className="text-[10px] sh-muted">{activityLabel}</span>
          )}
          <button
            type="button"
            aria-label={t("more")}
            onClick={(e) => {
              e.preventDefault();
              e.stopPropagation();
              setMenuOpen(true);
            }}
            className="ml-1 hidden size-5 items-center justify-center rounded sh-muted hover:bg-black/10 group-hover:flex dark:hover:bg-white/15"
          >
            <IconDots className="size-3.5" />
          </button>
        </>
      )}
      {collapsed && item.unread_count > 0 && (
        <span className="absolute right-1 top-1 size-1.5 rounded-full bg-red-500" />
      )}
    </Link>
  );

  const menu = (
    <Popover open={menuOpen} onOpenChange={setMenuOpen}>
      <PopoverTrigger asChild>
        <span className="absolute right-1 top-1/2 -translate-y-1/2" />
      </PopoverTrigger>
      <PopoverContent side="right" align="start" className="w-44 p-1.5">
        <button
          type="button"
          onClick={() => {
            setMenuOpen(false);
            togglePin.mutate({
              type: item.type,
              id: item.id,
              pinned: !item.pinned,
            });
          }}
          className="flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-[12px] hover:bg-black/5 dark:hover:bg-white/10"
        >
          {item.pinned ? (
            <IconPinnedOff className="size-3.5 sh-muted" />
          ) : (
            <IconPin className="size-3.5 sh-muted" />
          )}
          <span>{item.pinned ? t("unpin") : t("pin")}</span>
        </button>
        <Link
          href={item.type === "session" ? chatHref(item) : detailHref(item)}
          onClick={() => setMenuOpen(false)}
          className="flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-[12px] hover:bg-black/5 dark:hover:bg-white/10"
        >
          <IconInfoCircle className="size-3.5 sh-muted" />
          <span>{t("details")}</span>
        </Link>
        <button
          type="button"
          onClick={() => {
            setMenuOpen(false);
            unstar.mutate({ type: item.type, id: item.id });
          }}
          className="flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-[12px] text-red-600 hover:bg-red-500/10"
        >
          <IconX className="size-3.5" />
          <span>{t("unstar")}</span>
        </button>
      </PopoverContent>
    </Popover>
  );

  if (collapsed) {
    return (
      <Tooltip>
        <TooltipTrigger asChild>{row}</TooltipTrigger>
        <TooltipContent side="right">
          <span className="font-medium">{item.name}</span>
          <span className="ml-2 sh-muted">{seed}</span>
        </TooltipContent>
      </Tooltip>
    );
  }

  return (
    <div className="relative shrink-0">
      {row}
      {menu}
    </div>
  );
}
