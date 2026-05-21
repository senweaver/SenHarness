"use client";

import { Link, usePathname } from "@/lib/navigation";
import { useTranslations } from "next-intl";
import { cn } from "@/lib/utils";

interface NavItem {
  href: string;
  label: string;
}

export function WorkspaceSettingsLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  const pathname = usePathname();
  const t = useTranslations();

  const items: NavItem[] = [
    { href: "/settings/workspace/branding", label: t("settings.branding.title") },
    { href: "/settings/workspace/quota", label: t("settings.workspaceQuota.title") },
    { href: "/settings/workspace/members", label: t("settings.members.title") },
    { href: "/settings/workspace/departments", label: t("settings.departments.title") },
    { href: "/settings/workspace/providers", label: t("settings.providers.title") },
    { href: "/settings/workspace/search-providers", label: t("settings.searchProviders.title") },
    { href: "/settings/workspace/runtimes", label: t("settings.runtimes.title") },
    { href: "/settings/workspace/mcp", label: t("mcp.navLabel") },
    { href: "/settings/workspace/skills", label: t("curatorSettings.navLabel") },
    { href: "/settings/workspace/governance", label: t("settings.governance.navLabel") },
    { href: "/settings/workspace/memory", label: t("settings.workspaceMemory.navLabel") },
    { href: "/settings/approvals", label: t("settings.approvalsNavLabel") },
    { href: "/settings/audit", label: t("settings.audit.navLabel") },
    { href: "/settings/moderation", label: t("settings.moderation.navLabel") },
    { href: "/settings/cross-platform", label: t("settings.crossPlatform.navLabel") },
    { href: "/settings/secrets", label: t("settings.secrets.navLabel") },
  ];

  return (
    <div className="flex flex-1 overflow-hidden">
      <aside className="w-60 shrink-0 overflow-y-auto border-r p-3">
        <p className="mb-1 px-2 text-[11px] font-medium sh-muted">
          {t("settings.scope.workspace")}
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
