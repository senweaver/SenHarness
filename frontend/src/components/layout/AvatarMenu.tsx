"use client";

import {
  IconActivity,
  IconBook2,
  IconLogout,
  IconRefresh,
  IconShieldCheck,
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
import { useOnboardingStore } from "@/stores/onboarding-store";
import { useWorkspaceStore } from "@/stores/workspace-store";
import { useMe } from "@/hooks/use-me";
import { cn } from "@/lib/utils";
import { ThemeSubMenu } from "./ThemeToggle";
import { LanguageSubMenu } from "./LanguageSwitcher";

export function AvatarMenu({ collapsed }: { collapsed: boolean }) {
  const t = useTranslations("avatar");
  const { data: me } = useMe();
  const restartOnboarding = useOnboardingStore((s) => s.restart);
  const activeWorkspace = useWorkspaceStore((s) =>
    s.workspaces.find((w) => w.id === s.activeWorkspaceId),
  );

  const isPlatformAdmin = me?.platform_role === "platform_admin";
  const isWorkspaceAdmin =
    me?.current_role === "owner" || me?.current_role === "admin";

  const signOut = async () => {
    try {
      await api.post("/api/v1/auth/logout");
    } catch {
      // ignore
    }
    useAuthStore.getState().clear();
    useWorkspaceStore.getState().clear();
    useOnboardingStore.getState().close({ clear: true });
    window.location.href = "/login";
  };

  const name = me?.name ?? "Guest";
  const initial = name.slice(0, 1).toUpperCase();

  return (
    <DropdownMenu>
      <DropdownMenuTrigger
        className={cn(
          "sh-nav-item flex w-full items-center rounded-md text-[13px] sh-menu-text",
          collapsed
            ? "h-[38px] w-[38px] mx-auto justify-center px-0"
            : "h-[40px] gap-2 px-2",
        )}
      >
        <Avatar className="size-[26px]">
          {me?.avatar_url && <AvatarImage src={me.avatar_url} alt={name} />}
          <AvatarFallback>{initial}</AvatarFallback>
        </Avatar>
        {!collapsed && (
          <div className="flex min-w-0 flex-1 flex-col items-start text-left">
            <span className="w-full truncate text-[13px] font-medium leading-tight">
              {name}
            </span>
            <span className="w-full truncate text-[11px] sh-muted leading-tight">
              {me?.email}
            </span>
          </div>
        )}
      </DropdownMenuTrigger>

      <DropdownMenuContent align="start" side="top" className="w-60">
        <DropdownMenuLabel>
          <div className="flex flex-col">
            <span className="text-sm font-medium text-[rgb(var(--color-fg))]">
              {name}
            </span>
            <span className="text-[11px] sh-muted">
              {me?.email}
              {activeWorkspace && <> · {activeWorkspace.name}</>}
            </span>
          </div>
        </DropdownMenuLabel>

        <DropdownMenuSeparator />
        <DropdownMenuItem asChild>
          <Link href="/settings/profile">
            <IconUser className="size-4" />
            {t("accountSettings")}
          </Link>
        </DropdownMenuItem>
        {(isPlatformAdmin || isWorkspaceAdmin) && (
          <DropdownMenuItem asChild>
            <Link href="/settings/system/jobs">
              <IconActivity className="size-4" />
              {t("backgroundJobs")}
            </Link>
          </DropdownMenuItem>
        )}
        {isPlatformAdmin && (
          <DropdownMenuItem asChild>
            <Link href="/admin">
              <IconShieldCheck className="size-4" />
              {t("platformAdmin")}
            </Link>
          </DropdownMenuItem>
        )}

        <DropdownMenuSeparator />
        <ThemeSubMenu />
        <LanguageSubMenu />

        <DropdownMenuSeparator />
        <DropdownMenuItem onClick={() => restartOnboarding()}>
          <IconRefresh className="size-4" />
          {t("restartOnboarding")}
        </DropdownMenuItem>
        <DropdownMenuItem asChild>
          <a
            href="https://github.com/senweaver/SenHarness"
            target="_blank"
            rel="noreferrer"
          >
            <IconBook2 className="size-4" />
            {t("help")}
          </a>
        </DropdownMenuItem>

        <DropdownMenuSeparator />
        <DropdownMenuItem onClick={signOut}>
          <IconLogout className="size-4" />
          {t("signOut")}
        </DropdownMenuItem>
      </DropdownMenuContent>
    </DropdownMenu>
  );
}
