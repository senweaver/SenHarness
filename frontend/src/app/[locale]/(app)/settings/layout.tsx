"use client";

import { Link } from "@/lib/navigation";
import { usePathname } from "@/lib/navigation";
import { useTranslations } from "next-intl";
import { cn } from "@/lib/utils";

interface NavGroup {
  label: string;
  items: { href: string; label: string }[];
}

export default function SettingsLayout({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const t = useTranslations();

  const groups: NavGroup[] = [
    {
      label: t("avatar.profile"),
      items: [
        { href: "/settings/profile", label: t("avatar.profile") },
        {
          href: "/settings/profile/soul",
          label: t("settings.soul.navLabel"),
        },
        { href: "/settings/appearance", label: t("avatar.appearance") },
        { href: "/settings/shortcuts", label: t("avatar.shortcuts") },
        { href: "/settings/billing", label: t("avatar.creditsPlan") },
      ],
    },
    {
      label: t("avatar.workspaceSettings"),
      items: [
        { href: "/settings/workspace/branding", label: t("settings.branding.title") },
        { href: "/settings/workspace/members", label: t("settings.members.title") },
        { href: "/settings/workspace/departments", label: t("settings.departments.title") },
        { href: "/settings/workspace/providers", label: t("settings.providers.title") },
        { href: "/settings/workspace/runtimes", label: t("settings.runtimes.title") },
        { href: "/agents", label: t("settings.agents.title") },
        { href: "/squads", label: t("settings.squads.title") },
        { href: "/settings/channels", label: t("settings.channels.navLabel") },
        { href: "/flows", label: t("flows.navLabel") },
        { href: "/knowledge", label: t("knowledge.navLabel") },
        { href: "/settings/skills", label: t("settings.skills.title") },
        { href: "/settings/memory", label: t("settings.memory.title") },
        { href: "/settings/approvals", label: t("settings.approvalsNavLabel") },
        {
          href: "/settings/workspace/governance",
          label: t("settings.governance.navLabel"),
        },
        {
          href: "/settings/workspace/memory",
          label: t("settings.workspaceMemory.navLabel"),
        },
        { href: "/settings/audit", label: t("settings.audit.navLabel") },
        { href: "/settings/moderation", label: t("settings.moderation.navLabel") },
        { href: "/settings/usage", label: t("settings.usage.navLabel") },
        { href: "/settings/secrets", label: t("settings.secrets.navLabel") },
      ],
    },
  ];

  return (
    <div className="flex flex-1 overflow-hidden">
      <aside className="w-60 shrink-0 overflow-y-auto border-r p-3">
        {groups.map((g) => (
          <div key={g.label} className="mb-4">
            <p className="mb-1 px-2 text-[11px] font-medium sh-muted">{g.label}</p>
            {g.items.map((i) => (
              <Link
                key={i.href}
                href={i.href}
                className={cn(
                  "block rounded-md px-2 py-1.5 text-sm",
                  pathname?.startsWith(i.href)
                    ? "bg-black/5 font-medium dark:bg-white/10"
                    : "hover:bg-black/5 dark:hover:bg-white/10",
                )}
              >
                {i.label}
              </Link>
            ))}
          </div>
        ))}
      </aside>
      <section className="flex-1 overflow-y-auto p-6">{children}</section>
    </div>
  );
}
