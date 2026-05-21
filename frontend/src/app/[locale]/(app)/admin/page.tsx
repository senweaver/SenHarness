"use client";

import { Link } from "@/lib/navigation";
import {
  IconActivity,
  IconBuildingCommunity,
  IconMessage2,
  IconPuzzle,
  IconRobot,
  IconShield,
  IconSparkles,
  IconUsers,
} from "@tabler/icons-react";
import { useLocale, useTranslations } from "next-intl";

import { Badge } from "@/components/ui/badge";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { PageHeader } from "@/components/ui/page-header";
import { Skeleton } from "@/components/ui/skeleton";
import { useAdminStats } from "@/hooks/use-admin";

export default function AdminDashboardPage() {
  const t = useTranslations("admin.dashboard");
  const locale = useLocale();
  const { data, isLoading } = useAdminStats();

  const fmt = (n: number) => n.toLocaleString(locale);

  return (
    <div>
      <PageHeader title={t("title")} description={t("description")} />

      {isLoading && (
        <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
          {[0, 1, 2, 3, 4, 5, 6, 7].map((i) => (
            <Skeleton key={i} className="h-24" />
          ))}
        </div>
      )}

      {data && (
        <>
          <div className="mb-4 grid grid-cols-2 gap-3 sm:grid-cols-4">
            <StatCard
              icon={<IconUsers className="size-4" />}
              label={t("stats.identities")}
              value={fmt(data.identities_total)}
              hint={t("stats.identitiesHint", {
                active: fmt(data.identities_active),
                suspended: fmt(data.identities_suspended),
              })}
            />
            <StatCard
              icon={<IconBuildingCommunity className="size-4" />}
              label={t("stats.workspaces")}
              value={fmt(data.workspaces_total)}
              hint={t("stats.newInWeek", { n: fmt(data.new_workspaces_7d) })}
            />
            <StatCard
              icon={<IconMessage2 className="size-4" />}
              label={t("stats.sessions")}
              value={fmt(data.sessions_total)}
              hint={t("stats.messages", {
                n: fmt(data.messages_total),
              })}
            />
            <StatCard
              icon={<IconActivity className="size-4" />}
              label={t("stats.audit24h")}
              value={fmt(data.audit_events_24h)}
              hint={t("stats.audit24hHint")}
            />
            <StatCard
              icon={<IconRobot className="size-4" />}
              label={t("stats.agents")}
              value={fmt(data.agents_total)}
            />
            <StatCard
              icon={<IconSparkles className="size-4" />}
              label={t("stats.flows")}
              value={fmt(data.flows_total)}
            />
            <StatCard
              icon={<IconPuzzle className="size-4" />}
              label={t("stats.channels")}
              value={fmt(data.channels_total)}
            />
            <StatCard
              icon={<IconShield className="size-4" />}
              label={t("stats.platformAdmins")}
              value={fmt(data.platform_admins)}
              hint={t("stats.platformAdminsHint")}
            />
          </div>

          <div className="grid gap-3 lg:grid-cols-2">
            <Card>
              <CardHeader>
                <CardTitle className="text-base">{t("quickLinks.title")}</CardTitle>
                <CardDescription>{t("quickLinks.description")}</CardDescription>
              </CardHeader>
              <CardContent className="space-y-2 text-sm">
                <QuickLink
                  href="/admin/users"
                  label={t("quickLinks.users")}
                  count={data.identities_total}
                />
                <QuickLink
                  href="/admin/workspaces"
                  label={t("quickLinks.workspaces")}
                  count={data.workspaces_total}
                />
                <QuickLink
                  href="/settings/audit?scope=platform"
                  label={t("quickLinks.audit")}
                  count={data.audit_events_24h}
                />
              </CardContent>
            </Card>

            <Card>
              <CardHeader>
                <CardTitle className="text-base">{t("growth.title")}</CardTitle>
                <CardDescription>{t("growth.description")}</CardDescription>
              </CardHeader>
              <CardContent className="space-y-2 text-sm">
                <Row
                  label={t("growth.newIdentities7d")}
                  value={
                    <Badge variant="primary">
                      +{fmt(data.new_identities_7d)}
                    </Badge>
                  }
                />
                <Row
                  label={t("growth.newWorkspaces7d")}
                  value={
                    <Badge variant="primary">
                      +{fmt(data.new_workspaces_7d)}
                    </Badge>
                  }
                />
              </CardContent>
            </Card>
          </div>
        </>
      )}
    </div>
  );
}

function StatCard({
  icon,
  label,
  value,
  hint,
}: {
  icon: React.ReactNode;
  label: string;
  value: string;
  hint?: string;
}) {
  return (
    <Card>
      <CardContent className="py-3">
        <div className="mb-1 flex items-center gap-1.5 text-[11px] sh-muted">
          {icon}
          <span className="uppercase tracking-wide">{label}</span>
        </div>
        <div className="text-xl font-semibold tabular-nums">{value}</div>
        {hint && <div className="mt-0.5 text-[11px] sh-muted">{hint}</div>}
      </CardContent>
    </Card>
  );
}

function QuickLink({
  href,
  label,
  count,
}: {
  href: string;
  label: string;
  count: number;
}) {
  return (
    <Link
      href={href}
      className="flex items-center justify-between rounded-md px-2 py-1.5 hover:bg-black/5 dark:hover:bg-white/5"
    >
      <span>{label}</span>
      <Badge variant="outline">{count.toLocaleString()}</Badge>
    </Link>
  );
}

function Row({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <div className="flex items-center justify-between">
      <span className="text-[11px] sh-muted">{label}</span>
      {value}
    </div>
  );
}
