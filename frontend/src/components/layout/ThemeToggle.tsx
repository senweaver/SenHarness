"use client";

import {
  IconMoon,
  IconSun,
  IconDeviceDesktop,
  IconCircleHalf2,
} from "@tabler/icons-react";
import { useTheme } from "next-themes";
import { useTranslations } from "next-intl";
import {
  DropdownMenuItem,
  DropdownMenuSub,
  DropdownMenuSubContent,
  DropdownMenuSubTrigger,
} from "@/components/ui/dropdown-menu";

export function ThemeSubMenu() {
  const { setTheme, theme } = useTheme();
  const t = useTranslations("theme");

  return (
    <DropdownMenuSub>
      <DropdownMenuSubTrigger>
        <IconSun className="size-4" />
        <span>{t("label")}</span>
        <span className="ml-auto text-[11px] sh-muted">
          {theme === "system" ? "↺" : theme}
        </span>
      </DropdownMenuSubTrigger>
      <DropdownMenuSubContent>
        <DropdownMenuItem onClick={() => setTheme("light")}>
          <IconSun className="size-4" />
          {t("light")}
        </DropdownMenuItem>
        <DropdownMenuItem onClick={() => setTheme("dark")}>
          <IconMoon className="size-4" />
          {t("dark")}
        </DropdownMenuItem>
        <DropdownMenuItem onClick={() => setTheme("soft")}>
          <IconCircleHalf2 className="size-4" />
          {t("soft")}
        </DropdownMenuItem>
        <DropdownMenuItem onClick={() => setTheme("system")}>
          <IconDeviceDesktop className="size-4" />
          {t("system")}
        </DropdownMenuItem>
      </DropdownMenuSubContent>
    </DropdownMenuSub>
  );
}
