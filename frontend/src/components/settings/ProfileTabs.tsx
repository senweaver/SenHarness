"use client";

import { Link } from "@/lib/navigation";
import { useSearchParams } from "next/navigation";
import { useTranslations } from "next-intl";

import { cn } from "@/lib/utils";

export type ProfileTabKey = "profile" | "soul";

/**
 * `ProfileTabs` — single-route tab strip mounted at the top of
 * `/settings/profile`. Switching a tab updates `?tab=` so React state
 * is preserved (plan §6: one less page jump).
 */
export function ProfileTabs({ active }: { active: ProfileTabKey }) {
  const t = useTranslations();
  const searchParams = useSearchParams();

  const buildHref = (tab: ProfileTabKey) => {
    const params = new URLSearchParams(searchParams.toString());
    if (tab === "profile") params.delete("tab");
    else params.set("tab", tab);
    const qs = params.toString();
    return `/settings/profile${qs ? `?${qs}` : ""}`;
  };

  const tabs: Array<{ key: ProfileTabKey; label: string }> = [
    { key: "profile", label: t("avatar.profile") },
    { key: "soul", label: t("settings.soul.navLabel") },
  ];

  return (
    <div className="mb-4 flex gap-1 border-b">
      {tabs.map((tab) => {
        const isActive = tab.key === active;
        return (
          <Link
            key={tab.key}
            href={buildHref(tab.key)}
            className={cn(
              "border-b-2 px-3 py-2 text-sm font-medium transition-colors",
              isActive
                ? "border-[rgb(var(--color-primary))] text-[rgb(var(--color-primary))]"
                : "border-transparent sh-muted hover:text-[rgb(var(--color-fg))]",
            )}
            scroll={false}
          >
            {tab.label}
          </Link>
        );
      })}
    </div>
  );
}
