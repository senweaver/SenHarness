"use client";

import { Link } from "@/lib/navigation";
import { usePathname } from "@/lib/navigation";
import {
  IconBook,
  IconHome,
  IconPuzzle,
  IconSearch,
  IconShoppingBag,
} from "@tabler/icons-react";
import { useTranslations } from "next-intl";
import { cn } from "@/lib/utils";
import { useSidebarStore } from "@/stores/sidebar-store";
import { useCommandStore } from "@/stores/command-store";
import { RecentAgents } from "@/components/nav/RecentAgents";
import { RecentSessions } from "@/components/nav/RecentSessions";
import { TopLogo } from "./TopLogo";
import { AvatarMenu } from "./AvatarMenu";

interface NavItemProps {
  href: string;
  icon: React.ReactNode;
  label: string;
  active?: boolean;
  collapsed?: boolean;
}

function NavItem({ href, icon, label, active, collapsed }: NavItemProps) {
  return (
    <Link
      href={href}
      className={cn(
        "flex items-center gap-3 rounded-md px-2 py-2 text-sm transition-colors",
        active
          ? "bg-black/5 font-medium dark:bg-white/10"
          : "hover:bg-black/5 dark:hover:bg-white/10",
        collapsed && "justify-center px-0",
      )}
      title={collapsed ? label : undefined}
    >
      <span className="shrink-0">{icon}</span>
      {!collapsed && <span className="flex-1 truncate">{label}</span>}
    </Link>
  );
}

export function SiderNav() {
  const t = useTranslations();
  const pathname = usePathname();
  const collapsed = useSidebarStore((s) => s.collapsed);
  const openCommand = useCommandStore((s) => s.setOpen);

  const isActive = (p: string) =>
    pathname === p || pathname?.startsWith(p + "/") || pathname?.endsWith(p);

  return (
    <aside
      className={cn(
        "sticky top-0 flex h-screen flex-col border-r sh-card transition-[width] duration-150",
        collapsed ? "w-[56px]" : "w-[260px]",
      )}
    >
      <TopLogo />

      <div className="px-2 pt-2">
        <button
          onClick={() => openCommand(true)}
          className={cn(
            "flex w-full items-center gap-2 rounded-md border bg-transparent px-2 py-1.5 text-sm sh-muted hover:sh-card",
            collapsed && "justify-center px-0",
          )}
          aria-label={t("common.search")}
        >
          <IconSearch className="size-4" />
          {!collapsed && (
            <>
              <span className="flex-1 text-left">{t("common.search")}</span>
              <kbd className="rounded bg-black/5 px-1 text-[10px] dark:bg-white/10">Ctrl+K</kbd>
            </>
          )}
        </button>
      </div>

      <nav className="flex flex-col gap-0.5 px-2 pt-3">
        <NavItem
          href="/"
          icon={<IconHome className="size-4" />}
          label={t("nav.home")}
          active={pathname === "/" || pathname?.match(/^\/[a-z]{2}-[A-Z]{2}$/) != null}
          collapsed={collapsed}
        />
      </nav>

      <div className="flex-1 overflow-y-auto">
        <RecentAgents />
        <RecentSessions />

        <div className="border-t mx-2 my-3" />

        <nav className="flex flex-col gap-0.5 px-2">
          <NavItem
            href="/knowledge"
            icon={<IconBook className="size-4" />}
            label={t("nav.knowledge")}
            active={isActive("/knowledge")}
            collapsed={collapsed}
          />
          <NavItem
            href="/settings/skills"
            icon={<IconPuzzle className="size-4" />}
            label={t("nav.skills")}
            active={isActive("/settings/skills")}
            collapsed={collapsed}
          />
          <NavItem
            href="/marketplace"
            icon={<IconShoppingBag className="size-4" />}
            label={t("nav.marketplace")}
            active={isActive("/marketplace")}
            collapsed={collapsed}
          />
        </nav>
      </div>

      <div className="border-t p-2">
        <AvatarMenu />
      </div>
    </aside>
  );
}
