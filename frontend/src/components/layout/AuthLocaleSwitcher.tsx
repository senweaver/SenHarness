"use client";

import { IconChevronDown, IconLanguage } from "@tabler/icons-react";
import { useLocale, useTranslations } from "next-intl";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { usePathname } from "@/lib/navigation";
import { type Locale, locales } from "@/lib/i18n-config";
import { applyLocale } from "@/lib/locale";

const LABELS: Record<Locale, string> = {
  "en-US": "English",
  "zh-CN": "简体中文",
};

export function AuthLocaleSwitcher() {
  const locale = useLocale() as Locale;
  const pathname = usePathname() ?? "/";
  const t = useTranslations("avatar");

  return (
    <DropdownMenu>
      <DropdownMenuTrigger
        className="inline-flex items-center gap-1 rounded-md border bg-transparent px-2.5 py-1.5 text-xs sh-muted transition-colors hover:bg-black/5 focus:outline-none focus:ring-1 focus:ring-ring dark:hover:bg-white/10"
        aria-label={t("language")}
        data-testid="auth-locale-switcher"
      >
        <IconLanguage className="size-3.5" />
        <span>{LABELS[locale]}</span>
        <IconChevronDown className="size-3" />
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end" className="min-w-[10rem]">
        {locales.map((target) => (
          <DropdownMenuItem
            key={target}
            onSelect={() => {
              if (target === locale) return;
              applyLocale(pathname, target);
            }}
            data-testid={`auth-locale-option-${target}`}
            className={target === locale ? "font-semibold" : undefined}
          >
            <span className="w-14 text-[11px] uppercase tracking-wide sh-muted">
              {target}
            </span>
            <span>{LABELS[target]}</span>
          </DropdownMenuItem>
        ))}
      </DropdownMenuContent>
    </DropdownMenu>
  );
}
