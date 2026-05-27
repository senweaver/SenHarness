"use client";

import { IconLanguage } from "@tabler/icons-react";
import { useLocale, useTranslations } from "next-intl";
import { usePathname, useRouter } from "@/lib/navigation";
import {
  DropdownMenuItem,
  DropdownMenuSub,
  DropdownMenuSubContent,
  DropdownMenuSubTrigger,
} from "@/components/ui/dropdown-menu";
import { type Locale } from "@/lib/i18n-config";

const LABELS: Record<Locale, string> = {
  "zh-CN": "简体中文",
  "en-US": "English",
};

const ALL_LOCALES: Locale[] = ["zh-CN", "en-US"];

export function LanguageSubMenu() {
  const locale = useLocale();
  const router = useRouter();
  const pathname = usePathname() ?? "/";
  const t = useTranslations("avatar");

  // router.push from @/lib/navigation automatically preserves the current
  // locale. To switch locale we use the `locale` option.
  const switchTo = (target: Locale) => {
    router.push(pathname, { locale: target });
  };

  return (
    <DropdownMenuSub>
      <DropdownMenuSubTrigger>
        <IconLanguage className="size-4" />
        <span>{t("language")}</span>
        <span className="ml-auto text-[11px] sh-muted">{LABELS[locale as Locale]}</span>
      </DropdownMenuSubTrigger>
      <DropdownMenuSubContent>
        {ALL_LOCALES.map((l) => (
          <DropdownMenuItem key={l} onClick={() => switchTo(l)}>
            <span className="w-16">{l}</span>
            {LABELS[l]}
          </DropdownMenuItem>
        ))}
      </DropdownMenuSubContent>
    </DropdownMenuSub>
  );
}
