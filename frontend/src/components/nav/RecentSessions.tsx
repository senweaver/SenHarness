"use client";

import { Link } from "@/lib/navigation";
import { IconMessageCircle } from "@tabler/icons-react";
import { useTranslations } from "next-intl";
import { useRecentSessions } from "@/hooks/use-sessions";
import { useSidebarStore } from "@/stores/sidebar-store";

export function RecentSessions() {
  const t = useTranslations();
  const tChat = useTranslations("chat");
  const collapsed = useSidebarStore((s) => s.collapsed);
  const { data } = useRecentSessions(8);

  if (collapsed) return null;

  return (
    <div className="flex flex-col gap-0.5 px-2 pt-3">
      <div className="flex items-center justify-between px-2 pb-1 text-[11px] font-medium sh-muted">
        <span>{t("nav.recentSessions")}</span>
      </div>

      {(data ?? []).length === 0 && (
        <div className="px-2 py-1 text-xs sh-muted">{t("emptyStates.noSessions")}</div>
      )}

      {(data ?? []).map((s) => (
        <Link
          key={s.id}
          href={`/chat/${s.id}`}
          className="flex items-center gap-2 rounded-md px-2 py-1.5 text-sm hover:bg-black/5 dark:hover:bg-white/10"
        >
          <IconMessageCircle className="size-3.5 shrink-0 sh-muted" />
          <span className="truncate">{s.title ?? tChat("untitled")}</span>
        </Link>
      ))}

      {(data ?? []).length > 0 && (
        <Link
          href="/chat"
          className="mt-1 px-2 py-1 text-[11px] sh-muted hover:text-[rgb(var(--color-fg))]"
        >
          {t("nav.viewAllSessions")}
        </Link>
      )}
    </div>
  );
}
