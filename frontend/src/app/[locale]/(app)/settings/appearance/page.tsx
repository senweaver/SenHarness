"use client";

import { usePathname } from "@/lib/navigation";
import {
  IconDeviceDesktop,
  IconLanguage,
  IconLayoutSidebar,
  IconLayoutSidebarFilled,
  IconMoon,
  IconPalette,
  IconSun,
} from "@tabler/icons-react";
import { useLocale, useTranslations } from "next-intl";
import { useTheme } from "next-themes";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Switch } from "@/components/ui/switch";
import { PageHeader } from "@/components/ui/page-header";
import { locales, type Locale } from "@/lib/i18n-config";
import { applyLocale } from "@/lib/locale";
import { cn } from "@/lib/utils";
import { useSidebarStore } from "@/stores/sidebar-store";

const LOCALE_LABELS: Record<Locale, string> = {
  "zh-CN": "简体中文",
  "en-US": "English",
};

export default function AppearanceSettingsPage() {
  const t = useTranslations("settings.appearance");
  const { theme, setTheme } = useTheme();
  const locale = useLocale();
  const pathname = usePathname() ?? "/";
  const collapsed = useSidebarStore((s) => s.collapsed);
  const setCollapsed = useSidebarStore((s) => s.setCollapsed);

  const switchLocale = (target: Locale) => {
    if (target === locale) return;
    applyLocale(pathname, target);
  };

  return (
    <div>
      <PageHeader title={t("title")} description={t("description")} />

      {/* ─── Theme ─── */}
      <Card className="mb-3">
        <CardHeader>
          <CardTitle className="flex items-center gap-2 text-base">
            <IconPalette className="size-4" />
            {t("theme")}
          </CardTitle>
          <CardDescription>{t("themeDesc")}</CardDescription>
        </CardHeader>
        <CardContent>
          <div className="grid grid-cols-3 gap-2">
            <ThemeCard
              active={theme === "light"}
              onClick={() => setTheme("light")}
              label={t("light")}
              icon={<IconSun className="size-5" />}
              preview="bg-white text-neutral-900 border-neutral-200"
            />
            <ThemeCard
              active={theme === "dark"}
              onClick={() => setTheme("dark")}
              label={t("dark")}
              icon={<IconMoon className="size-5" />}
              preview="bg-neutral-950 text-neutral-50 border-neutral-800"
            />
            <ThemeCard
              active={theme === "system"}
              onClick={() => setTheme("system")}
              label={t("system")}
              icon={<IconDeviceDesktop className="size-5" />}
              preview="bg-gradient-to-br from-white to-neutral-900 text-neutral-600 border-neutral-300"
            />
          </div>
        </CardContent>
      </Card>

      {/* ─── Primary color (preview-only for now) ─── */}
      <Card className="mb-3">
        <CardHeader>
          <CardTitle className="flex items-center gap-2 text-base">
            <IconPalette className="size-4" />
            {t("primary")}
          </CardTitle>
          <CardDescription>{t("primaryDesc")}</CardDescription>
        </CardHeader>
        <CardContent>
          <div className="flex items-center gap-3">
            <div className="size-10 rounded-full sh-primary" />
            <div className="flex-1">
              <div className="text-sm font-medium">
                rgb(var(--color-primary))
              </div>
              <div className="text-[11px] sh-muted">{t("primaryHint")}</div>
            </div>
            <Badge variant="outline">{t("primaryWorkspaceOnly")}</Badge>
          </div>
        </CardContent>
      </Card>

      {/* ─── Language ─── */}
      <Card className="mb-3">
        <CardHeader>
          <CardTitle className="flex items-center gap-2 text-base">
            <IconLanguage className="size-4" />
            {t("language")}
          </CardTitle>
          <CardDescription>{t("languageDesc")}</CardDescription>
        </CardHeader>
        <CardContent>
          <div className="grid grid-cols-2 gap-2 sm:grid-cols-3">
            {locales.map((l) => (
              <button
                key={l}
                onClick={() => switchLocale(l)}
                className={cn(
                  "flex items-center gap-2 rounded-md border p-2.5 text-sm transition-colors",
                  l === locale
                    ? "border-[rgb(var(--color-primary))] bg-[rgb(var(--color-primary))]/5"
                    : "hover:bg-black/5 dark:hover:bg-white/5",
                )}
              >
                <span className="flex-1 text-left">
                  <span className="block font-medium">{LOCALE_LABELS[l]}</span>
                  <span className="block text-[11px] sh-muted">{l}</span>
                </span>
                {l === locale && (
                  <Badge variant="primary">{t("current")}</Badge>
                )}
              </button>
            ))}
          </div>
        </CardContent>
      </Card>

      {/* ─── Layout ─── */}
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2 text-base">
            <IconLayoutSidebar className="size-4" />
            {t("layout")}
          </CardTitle>
          <CardDescription>{t("layoutDesc")}</CardDescription>
        </CardHeader>
        <CardContent className="space-y-3">
          <div className="flex items-center justify-between rounded-md border p-3">
            <div>
              <div className="flex items-center gap-2 text-sm font-medium">
                {collapsed ? (
                  <IconLayoutSidebarFilled className="size-4" />
                ) : (
                  <IconLayoutSidebar className="size-4" />
                )}
                {t("sidebarCollapsed")}
              </div>
              <div className="text-[11px] sh-muted">
                {t("sidebarCollapsedHint")}
              </div>
            </div>
            <Switch checked={collapsed} onCheckedChange={setCollapsed} />
          </div>
        </CardContent>
      </Card>
    </div>
  );
}

function ThemeCard({
  active,
  onClick,
  label,
  icon,
  preview,
}: {
  active: boolean;
  onClick: () => void;
  label: string;
  icon: React.ReactNode;
  preview: string;
}) {
  return (
    <Button
      variant="outline"
      onClick={onClick}
      className={cn(
        "flex h-24 flex-col items-center justify-center gap-1.5 p-3",
        active &&
          "border-[rgb(var(--color-primary))] bg-[rgb(var(--color-primary))]/5",
      )}
    >
      <div
        className={cn(
          "flex size-10 items-center justify-center rounded-md border",
          preview,
        )}
      >
        {icon}
      </div>
      <span className="text-sm">{label}</span>
    </Button>
  );
}
