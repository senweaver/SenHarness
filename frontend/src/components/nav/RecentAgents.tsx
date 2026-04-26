"use client";

import { Link } from "@/lib/navigation";
import { IconRobot, IconSparkles } from "@tabler/icons-react";
import { useTranslations, useLocale } from "next-intl";
import { useRecentAgents } from "@/hooks/use-agents";
import { useSidebarStore } from "@/stores/sidebar-store";
import { relativeTime } from "@/lib/utils";
export function RecentAgents() {
  const t = useTranslations();
  const locale = useLocale();
  const collapsed = useSidebarStore((s) => s.collapsed);
  const { data, isLoading } = useRecentAgents(5);

  if (collapsed) {
    return (
      <div className="flex flex-col gap-1 px-2 pt-2">
        {(data ?? []).map((a) => (
          <Link
            key={a.id}
            href={`/chat/new?agent=${a.id}`}
            className="flex size-10 items-center justify-center rounded-md hover:bg-black/5 dark:hover:bg-white/10"
            title={`${a.name}${a.last_message_at ? " · " + relativeTime(a.last_message_at, locale) : ""}`}
          >
            {a.avatar_url ? (
              <img src={a.avatar_url} alt="" className="size-7 rounded-full" />
            ) : (
              <IconRobot className="size-4" />
            )}
          </Link>
        ))}
      </div>
    );
  }

  return (
    <div className="flex flex-col gap-0.5 px-2 pt-3">
      <div className="flex items-center justify-between px-2 pb-1 text-[11px] font-medium sh-muted">
        <span>{t("nav.recentAgents")}</span>
      </div>

      {isLoading && <div className="px-2 py-1 text-xs sh-muted">{t("common.loading")}</div>}

      {!isLoading && (data ?? []).length === 0 && (
        <div className="px-2 py-2 text-xs sh-muted">
          <p>{t("emptyStates.noAgentsRecent")}</p>
          <Link
            href="/agents"
            className="mt-1 inline-flex items-center gap-1 text-[rgb(var(--color-primary))] hover:underline"
          >
            <IconSparkles className="size-3" />
            {t("emptyStates.discoverAgents")}
          </Link>
        </div>
      )}

      {(data ?? []).map((a) => (
        <Link
          key={a.id}
          href={`/chat/new?agent=${a.id}`}
          className="group flex items-center gap-2 rounded-md px-2 py-1.5 text-sm hover:bg-black/5 dark:hover:bg-white/10"
        >
          {a.avatar_url ? (
            <img src={a.avatar_url} alt="" className="size-6 rounded-full" />
          ) : (
            <div className="flex size-6 items-center justify-center rounded-full bg-black/10 dark:bg-white/10">
              <IconRobot className="size-3.5" />
            </div>
          )}
          <span className="flex-1 truncate">{a.name}</span>
          {a.last_message_at && (
            <span className="text-[10px] sh-muted tabular-nums">
              {relativeTime(a.last_message_at, locale)}
            </span>
          )}
          {a.pinned && <span className="text-[10px] sh-muted">📌</span>}
        </Link>
      ))}

      {(data ?? []).length > 0 && (
        <Link
          href="/agents"
          className="mt-1 px-2 py-1 text-[11px] sh-muted hover:text-[rgb(var(--color-fg))]"
        >
          {t("nav.viewAllAgents")}
        </Link>
      )}
    </div>
  );
}
