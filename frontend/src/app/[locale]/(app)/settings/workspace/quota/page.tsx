"use client";

import { useTranslations } from "next-intl";

import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { PageHeader } from "@/components/ui/page-header";
import { useWorkspaceQuota } from "@/hooks/use-workspace-quota";

function formatPeriod(seconds: number): string {
  if (seconds % 3600 === 0) return `${seconds / 3600}h`;
  if (seconds % 60 === 0) return `${seconds / 60}m`;
  return `${seconds}s`;
}

export default function WorkspaceQuotaSettingsPage() {
  const t = useTranslations("settings.workspaceQuota");
  const { data: quota, isLoading } = useWorkspaceQuota();

  return (
    <div className="space-y-6">
      <PageHeader title={t("title")} description={t("description")} />

      <Card>
        <CardHeader>
          <CardTitle>{t("usageTitle")}</CardTitle>
          <CardDescription>{t("usageDescription")}</CardDescription>
        </CardHeader>
        <CardContent>
          {isLoading || !quota ? (
            <p className="text-sm sh-muted">{t("loading")}</p>
          ) : (
            <dl className="grid grid-cols-2 gap-4 text-sm md:grid-cols-3">
              <div>
                <dt className="sh-muted text-[11px] uppercase tracking-wide">
                  {t("metric.used")}
                </dt>
                <dd className="text-2xl font-semibold">{quota.used}</dd>
              </div>
              <div>
                <dt className="sh-muted text-[11px] uppercase tracking-wide">
                  {t("metric.limit")}
                </dt>
                <dd className="text-2xl font-semibold">{quota.limit}</dd>
              </div>
              <div>
                <dt className="sh-muted text-[11px] uppercase tracking-wide">
                  {t("metric.remaining")}
                </dt>
                <dd className="text-2xl font-semibold">{quota.remaining}</dd>
              </div>
              <div>
                <dt className="sh-muted text-[11px] uppercase tracking-wide">
                  {t("metric.sourceKind")}
                </dt>
                <dd className="font-mono text-[12px]">
                  {t(`sourceKind.${quota.source_kind}`)}
                </dd>
              </div>
              <div>
                <dt className="sh-muted text-[11px] uppercase tracking-wide">
                  {t("metric.rateWindow")}
                </dt>
                <dd className="text-[12px]">
                  {quota.rate_window_used} / {quota.rate_window_limit} per{" "}
                  {formatPeriod(quota.rate_window_seconds)}
                </dd>
              </div>
              <div>
                <dt className="sh-muted text-[11px] uppercase tracking-wide">
                  {t("metric.canCreate")}
                </dt>
                <dd className="text-[12px]">
                  {quota.creation_kind_allowed ? t("yes") : t("no")}
                </dd>
              </div>
            </dl>
          )}
        </CardContent>
      </Card>

      {quota?.override_active && (
        <Card>
          <CardHeader>
            <CardTitle>{t("overrideTitle")}</CardTitle>
            <CardDescription>
              {quota.grandfathered
                ? t("overrideGrandfathered")
                : t("overrideAdmin")}
            </CardDescription>
          </CardHeader>
        </Card>
      )}

      {!quota?.creation_kind_allowed && (
        <Card>
          <CardHeader>
            <CardTitle>{t("requestTitle")}</CardTitle>
            <CardDescription>{t("requestDescription")}</CardDescription>
          </CardHeader>
        </Card>
      )}
    </div>
  );
}
