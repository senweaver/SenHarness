"use client";

import { Link, usePathname } from "@/lib/navigation";
import { useTranslations } from "next-intl";
import { cn } from "@/lib/utils";

interface NavItem {
  href: string;
  label: string;
}

export function AccountSettingsLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  const pathname = usePathname();
  const t = useTranslations();

  const items: NavItem[] = [
    { href: "/settings/profile", label: t("avatar.profile") },
    { href: "/settings/appearance", label: t("avatar.appearance") },
    { href: "/settings/notifications", label: t("notification.prefsTitle") },
    { href: "/settings/shortcuts", label: t("avatar.shortcuts") },
    { href: "/settings/usage", label: t("settings.usage.navLabel") },
    { href: "/settings/billing", label: t("avatar.creditsPlan") },
  ];

  return (
    <div className="flex flex-1 overflow-hidden">
      <aside className="w-60 shrink-0 overflow-y-auto border-r p-3">
        <p className="mb-1 px-2 text-[11px] font-medium sh-muted">
          {t("settings.scope.account")}
        </p>
        {items.map((item) => (
          <Link
            key={item.href}
            href={item.href}
            className={cn(
              "block rounded-md px-2 py-1.5 text-sm",
              pathname?.startsWith(item.href)
                ? "bg-black/5 font-medium dark:bg-white/10"
                : "hover:bg-black/5 dark:hover:bg-white/10",
            )}
          >
            {item.label}
          </Link>
        ))}
      </aside>
      <section className="flex-1 overflow-y-auto p-6">{children}</section>
    </div>
  );
}
