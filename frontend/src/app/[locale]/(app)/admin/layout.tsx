"use client";

import { useEffect } from "react";
import { Link } from "@/lib/navigation";
import { usePathname, useRouter } from "@/lib/navigation";
import {
  IconActivity,
  IconBuildingCommunity,
  IconGauge,
  IconKey,
  IconScale,
  IconShieldCheck,
  IconShieldLock,
  IconUsers,
} from "@tabler/icons-react";
import { useTranslations } from "next-intl";
import { Skeleton } from "@/components/ui/skeleton";
import { useMe } from "@/hooks/use-me";
import { cn } from "@/lib/utils";

/** Admin section is gated to platform_admin. Non-admins get bounced home. */
export default function AdminLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  const t = useTranslations("admin");
  const pathname = usePathname() ?? "/admin";
  const router = useRouter();
  const { data: me, isLoading } = useMe();

  useEffect(() => {
    if (!isLoading && me && me.platform_role !== "platform_admin") {
      router.replace("/");
    }
  }, [isLoading, me, router]);

  if (isLoading || !me) {
    return (
      <div className="p-6">
        <Skeleton className="h-24" />
      </div>
    );
  }
  if (me.platform_role !== "platform_admin") {
    return null;
  }

  const items = [
    { href: "/admin", label: t("nav.dashboard"), icon: <IconGauge className="size-4" /> },
    { href: "/admin/users", label: t("nav.users"), icon: <IconUsers className="size-4" /> },
    {
      href: "/admin/workspaces",
      label: t("nav.workspaces"),
      icon: <IconBuildingCommunity className="size-4" />,
    },
    {
      href: "/admin/approvals",
      label: t("nav.approvals"),
      icon: <IconShieldCheck className="size-4" />,
    },
    {
      href: "/admin/keyring",
      label: t("nav.keyring"),
      icon: <IconKey className="size-4" />,
    },
    {
      href: "/admin/governance",
      label: t("nav.governance"),
      icon: <IconScale className="size-4" />,
    },
    {
      href: "/admin/observability",
      label: t("nav.observability"),
      icon: <IconActivity className="size-4" />,
    },
  ];

  return (
    <div className="flex flex-1 overflow-hidden">
      <aside className="w-60 shrink-0 overflow-y-auto border-r p-3">
        <div className="mb-3 flex items-center gap-2 px-2 text-xs font-medium">
          <IconShieldLock className="size-4 text-amber-500" />
          {t("title")}
        </div>
        <nav className="flex flex-col gap-0.5">
          {items.map((i) => {
            const active = pathname === i.href;
            return (
              <Link
                key={i.href}
                href={i.href}
                className={cn(
                  "flex items-center gap-2 rounded-md px-2 py-1.5 text-sm transition-colors",
                  active
                    ? "bg-black/5 font-medium dark:bg-white/10"
                    : "hover:bg-black/5 dark:hover:bg-white/5",
                )}
              >
                {i.icon}
                {i.label}
              </Link>
            );
          })}
        </nav>
      </aside>
      <section className="flex-1 overflow-y-auto p-6">{children}</section>
    </div>
  );
}
