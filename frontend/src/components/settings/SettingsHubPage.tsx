"use client";

import { Link } from "@/lib/navigation";
import {
  IconBuildingCommunity,
  IconShieldCheck,
  IconUser,
} from "@tabler/icons-react";
import { useTranslations } from "next-intl";

import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { PageHeader } from "@/components/ui/page-header";
import { useMe } from "@/hooks/use-me";

export function SettingsHubPage() {
  const t = useTranslations("settings.hub");
  const { data: me } = useMe();
  const isPlatformAdmin = me?.platform_role === "platform_admin";

  return (
    <div className="p-6">
      <PageHeader title={t("title")} description={t("description")} />
      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
        <HubCard
          href="/settings/profile"
          icon={<IconUser className="size-5" />}
          title={t("accountTitle")}
          description={t("accountDescription")}
        />
        <HubCard
          href="/settings/workspace/branding"
          icon={<IconBuildingCommunity className="size-5" />}
          title={t("workspaceTitle")}
          description={t("workspaceDescription")}
        />
        {isPlatformAdmin && (
          <HubCard
            href="/admin"
            icon={<IconShieldCheck className="size-5" />}
            title={t("platformTitle")}
            description={t("platformDescription")}
          />
        )}
      </div>
    </div>
  );
}

function HubCard({
  href,
  icon,
  title,
  description,
}: {
  href: string;
  icon: React.ReactNode;
  title: string;
  description: string;
}) {
  return (
    <Card className="relative transition-colors hover:bg-black/[0.02] dark:hover:bg-white/[0.02]">
      <Link href={href} className="absolute inset-0" aria-label={title} />
      <CardHeader>
        <span className="flex size-9 items-center justify-center rounded-md bg-[rgb(var(--color-primary)/0.12)] text-[rgb(var(--color-primary))]">
          {icon}
        </span>
        <CardTitle className="text-base">{title}</CardTitle>
        <CardDescription>{description}</CardDescription>
      </CardHeader>
      <CardContent />
    </Card>
  );
}
