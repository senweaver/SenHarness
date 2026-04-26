"use client";

import {
  IconBell,
  IconBook2,
  IconCreditCard,
  IconFlask,
  IconGift,
  IconKeyboard,
  IconLogout,
  IconRoute2,
  IconSettings,
  IconShield,
  IconStar,
  IconUser,
} from "@tabler/icons-react";
import { Link } from "@/lib/navigation";
import { useTranslations } from "next-intl";
import { Avatar, AvatarFallback, AvatarImage } from "@/components/ui/avatar";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { api } from "@/lib/api";
import { useAuthStore } from "@/stores/auth-store";
import { useWorkspaceStore } from "@/stores/workspace-store";
import { useMe } from "@/hooks/use-me";
import { useApprovalsCount } from "@/hooks/use-approvals";
import { useSidebarStore } from "@/stores/sidebar-store";
import { cn } from "@/lib/utils";
import { ThemeSubMenu } from "./ThemeToggle";
import { LanguageSubMenu } from "./LanguageSwitcher";
import { WorkspaceSwitcherSubMenu } from "./WorkspaceSwitcher";

export function AvatarMenu() {
  const t = useTranslations();
  const { data: me } = useMe();
  const collapsed = useSidebarStore((s) => s.collapsed);
  const active = useWorkspaceStore((s) => s.activeWorkspaceId);
  const activeWorkspace = useWorkspaceStore((s) =>
    s.workspaces.find((w) => w.id === s.activeWorkspaceId),
  );

  const signOut = async () => {
    try {
      await api.post("/api/v1/auth/logout");
    } catch {
      // ignore
    }
    useAuthStore.getState().clear();
    useWorkspaceStore.getState().clear();
    window.location.href = "/login";
  };

  const name = me?.name ?? "Guest";
  const initial = name.slice(0, 1).toUpperCase();
  const isPlatformAdmin = me?.platform_role === "platform_admin";
  const { data: approvalsCount } = useApprovalsCount();
  const pendingCount = approvalsCount?.pending ?? 0;
  void active; // subscribe so menu re-renders on workspace switch

  return (
    <DropdownMenu>
      <DropdownMenuTrigger
        className={cn(
          "flex w-full items-center gap-2 rounded-md px-1.5 py-1.5 text-sm hover:bg-black/5 dark:hover:bg-white/10",
          collapsed && "justify-center",
        )}
      >
        <Avatar>
          {me?.avatar_url && <AvatarImage src={me.avatar_url} alt={name} />}
          <AvatarFallback>{initial}</AvatarFallback>
        </Avatar>
        {!collapsed && (
          <div className="flex min-w-0 flex-1 flex-col items-start text-left">
            <span className="w-full truncate text-sm font-semibold">
              {activeWorkspace?.name ?? "—"}
            </span>
            <span className="w-full truncate text-[11px] sh-muted">{name}</span>
          </div>
        )}
        {!collapsed && (
          <div className="relative flex size-6 items-center justify-center">
            <IconBell className="size-4" />
            {pendingCount > 0 && (
              <span className="absolute -top-0.5 -right-0.5 flex h-3 min-w-3 items-center justify-center rounded-full bg-red-500 px-1 text-[8px] font-bold text-white">
                {pendingCount > 99 ? "99+" : pendingCount}
              </span>
            )}
          </div>
        )}
      </DropdownMenuTrigger>

      <DropdownMenuContent align="start" side="top" className="w-64">
        <DropdownMenuLabel>
          <div className="flex flex-col">
            <span className="text-sm font-medium text-[rgb(var(--color-fg))]">{name}</span>
            <span className="text-[11px]">
              {me?.email}
              {activeWorkspace && <> · {activeWorkspace.name}</>}
            </span>
          </div>
        </DropdownMenuLabel>

        <DropdownMenuSeparator />
        <DropdownMenuItem asChild>
          <Link href="/settings/profile">
            <IconUser className="size-4" />
            {t("avatar.profile")}
          </Link>
        </DropdownMenuItem>
        <DropdownMenuItem asChild>
          <Link href="/agents">
            <IconStar className="size-4" />
            {t("avatar.favorites")}
          </Link>
        </DropdownMenuItem>
        <DropdownMenuItem asChild>
          <Link href="/approvals" className="flex items-center gap-2">
            <IconBell className="size-4" />
            <span>{t("avatar.todosApprovals")}</span>
            {pendingCount > 0 && (
              <span className="ml-auto inline-flex min-w-5 items-center justify-center rounded-full bg-red-500 px-1.5 py-[1px] text-[10px] font-bold leading-none text-white">
                {pendingCount > 99 ? "99+" : pendingCount}
              </span>
            )}
          </Link>
        </DropdownMenuItem>

        <DropdownMenuSeparator />
        <ThemeSubMenu />
        <LanguageSubMenu />
        <DropdownMenuItem asChild>
          <Link href="/settings/shortcuts">
            <IconKeyboard className="size-4" />
            {t("avatar.shortcuts")}
          </Link>
        </DropdownMenuItem>

        <DropdownMenuSeparator />
        <WorkspaceSwitcherSubMenu />
        <DropdownMenuItem asChild>
          <Link href="/settings/workspace/general">
            <IconSettings className="size-4" />
            {t("avatar.workspaceSettings")}
          </Link>
        </DropdownMenuItem>
        {isPlatformAdmin && (
          <DropdownMenuItem asChild>
            <Link href="/admin">
              <IconShield className="size-4" />
              {t("avatar.platformAdmin")}
            </Link>
          </DropdownMenuItem>
        )}

        <DropdownMenuSeparator />
        <DropdownMenuItem asChild>
          <Link href="/flows">
            <IconRoute2 className="size-4" />
            {t("nav.flows")}
          </Link>
        </DropdownMenuItem>
        <DropdownMenuItem asChild>
          <Link href="/batch">
            <IconFlask className="size-4" />
            {t("nav.batch")}
          </Link>
        </DropdownMenuItem>

        <DropdownMenuSeparator />
        <DropdownMenuItem asChild>
          <Link href="/settings/billing">
            <IconCreditCard className="size-4" />
            {t("avatar.creditsPlan")}
          </Link>
        </DropdownMenuItem>
        <DropdownMenuItem asChild>
          <Link href="/settings/workspace/members">
            <IconGift className="size-4" />
            {t("avatar.invite")}
          </Link>
        </DropdownMenuItem>
        <DropdownMenuItem asChild>
          <a href="https://github.com/senweaver/SenHarness" target="_blank" rel="noreferrer">
            <IconBook2 className="size-4" />
            {t("avatar.help")}
          </a>
        </DropdownMenuItem>

        <DropdownMenuSeparator />
        <DropdownMenuItem onClick={signOut}>
          <IconLogout className="size-4" />
          {t("avatar.signOut")}
        </DropdownMenuItem>
      </DropdownMenuContent>
    </DropdownMenu>
  );
}
