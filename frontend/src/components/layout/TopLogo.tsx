"use client";

import { Link } from "@/lib/navigation";
import { IconLayoutSidebarLeftCollapse, IconLayoutSidebarLeftExpand } from "@tabler/icons-react";
import { useTranslations } from "next-intl";
import { Button } from "@/components/ui/button";
import { useSidebarStore } from "@/stores/sidebar-store";
import { cn } from "@/lib/utils";

export function TopLogo() {
  const t = useTranslations();
  const collapsed = useSidebarStore((s) => s.collapsed);
  const toggle = useSidebarStore((s) => s.toggleCollapsed);

  return (
    <div
      className={cn(
        "flex h-14 items-center border-b px-3",
        collapsed ? "justify-center" : "justify-between",
      )}
    >
      <Link href="/" className="flex items-center gap-2">
        <div className="flex size-7 items-center justify-center rounded-md sh-primary text-xs font-bold">
          S
        </div>
        {!collapsed && (
          <span className="text-sm font-semibold tracking-tight">{t("app.name")}</span>
        )}
      </Link>
      {!collapsed && (
        <Button
          variant="ghost"
          size="icon"
          onClick={toggle}
          aria-label={t("nav.collapse")}
          className="size-7"
        >
          <IconLayoutSidebarLeftCollapse className="size-4" />
        </Button>
      )}
      {collapsed && (
        <Button
          variant="ghost"
          size="icon"
          onClick={toggle}
          aria-label={t("nav.expand")}
          className="absolute left-1/2 top-16 size-6 -translate-x-1/2 rounded-full border sh-card"
        >
          <IconLayoutSidebarLeftExpand className="size-3" />
        </Button>
      )}
    </div>
  );
}
