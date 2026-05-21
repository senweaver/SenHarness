"use client";

import { useMemo } from "react";
import { Link, usePathname } from "@/lib/navigation";
import { IconLayoutDashboard, IconRadar2 } from "@tabler/icons-react";
import { useTranslations } from "next-intl";

import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { cn } from "@/lib/utils";
import { useSidebarStore } from "@/stores/sidebar-store";

import { MySection } from "./MySection";
import { NewMenuButton } from "./NewMenuButton";
import { RuntimePulseBar } from "./RuntimePulseBar";
import { UserFooter } from "./UserFooter";
import { WorkspaceSwitcherHeader } from "./WorkspaceSwitcherHeader";

interface NavItemDef {
  href: string;
  label: string;
  icon: React.ReactNode;
  match?: (pathname: string) => boolean;
  badge?: React.ReactNode;
}

function defaultMatch(href: string) {
  return (pathname: string | null) => {
    if (!pathname) return false;
    if (href === "/") return /^\/(?:[a-z]{2}-[A-Z]{2})?$/.test(pathname);
    return pathname === href || pathname.startsWith(href + "/");
  };
}

export function SiderNav() {
  const t = useTranslations("nav");
  const pathname = usePathname();
  const collapsed = useSidebarStore((s) => s.collapsed);
  const toggleCollapsed = useSidebarStore((s) => s.toggleCollapsed);

  const coreItems: NavItemDef[] = useMemo(
    () => [
      {
        href: "/",
        label: t("dashboard"),
        icon: <IconLayoutDashboard className="size-[18px] shrink-0" />,
        match: defaultMatch("/"),
      },
      {
        href: "/agent-view",
        label: t("agentView"),
        icon: <IconRadar2 className="size-[18px] shrink-0" />,
      },
    ],
    [t],
  );

  return (
    <aside
      className={cn(
        "sh-sidebar-surface relative sticky top-0 flex h-screen shrink-0 flex-col",
        "transition-[width] ease-out",
      )}
      style={{
        width: collapsed
          ? "var(--sh-sidebar-collapsed-width)"
          : "var(--sh-sidebar-width)",
        transitionDuration: "var(--sh-transition-duration)",
      }}
      aria-label="Primary"
    >
      <WorkspaceSwitcherHeader
        collapsed={collapsed}
        onToggleCollapsed={toggleCollapsed}
      />

      <div className={cn("px-2", collapsed ? "pt-2" : "pt-3")}>
        <NewMenuButton collapsed={collapsed} />
      </div>

      <nav className="mt-2 flex flex-col px-2">
        {coreItems.map((item) => (
          <NavItem
            key={item.href}
            item={item}
            pathname={pathname}
            collapsed={collapsed}
          />
        ))}
        {!collapsed && <RuntimePulseBar />}
      </nav>

      <div className="mt-1 border-t" />

      <MySection collapsed={collapsed} />

      <UserFooter collapsed={collapsed} />
    </aside>
  );
}

function NavItem({
  item,
  pathname,
  collapsed,
}: {
  item: NavItemDef;
  pathname: string | null;
  collapsed: boolean;
}) {
  const matcher = item.match ?? defaultMatch(item.href);
  const active = matcher(pathname ?? "");
  const content = (
    <Link
      href={item.href}
      aria-current={active ? "page" : undefined}
      className={cn(
        "sh-nav-item group relative flex items-center rounded-md text-[14px]",
        active ? "sh-nav-active" : "sh-menu-text",
        collapsed
          ? "h-[44px] w-[44px] mx-auto justify-center px-0"
          : "h-[44px] gap-3 px-3",
      )}
    >
      {item.icon}
      {!collapsed && <span className="flex-1 truncate">{item.label}</span>}
      {item.badge}
    </Link>
  );

  if (!collapsed) return content;

  return (
    <Tooltip>
      <TooltipTrigger asChild>{content}</TooltipTrigger>
      <TooltipContent side="right">{item.label}</TooltipContent>
    </Tooltip>
  );
}

