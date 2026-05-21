"use client";

import { IconSettings } from "@tabler/icons-react";
import { useTranslations } from "next-intl";

import { Link } from "@/lib/navigation";
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip";
import { cn } from "@/lib/utils";

import { AvatarMenu } from "./AvatarMenu";
import { NotificationBell } from "./NotificationBell";

interface UserFooterProps {
  collapsed: boolean;
}

export function UserFooter({ collapsed }: UserFooterProps) {
  return (
    <div className="border-t p-2">
      <div
        className={cn(
          "flex items-center gap-1",
          collapsed ? "flex-col" : "flex-row",
        )}
      >
        <div className={collapsed ? "" : "min-w-0 flex-1"}>
          <AvatarMenu collapsed={collapsed} />
        </div>
        <SettingsButton />
        <NotificationBell />
      </div>
    </div>
  );
}

function SettingsButton() {
  const t = useTranslations("userFooter");
  const label = t("settings");
  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <Link
          href="/settings/workspace/branding"
          aria-label={label}
          className="flex size-8 items-center justify-center rounded-md hover:bg-black/5 dark:hover:bg-white/10"
        >
          <IconSettings className="size-4" />
        </Link>
      </TooltipTrigger>
      <TooltipContent side="top">{label}</TooltipContent>
    </Tooltip>
  );
}
