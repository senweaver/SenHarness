"use client";

import {
  IconActivity,
  IconCash,
  IconCoin,
  IconTrendingUp,
} from "@tabler/icons-react";
import { useTranslations } from "next-intl";

interface MetricsGridProps {
  totalTokens?: number;
  estimatedCost?: number | string;
  weekChangePct?: number;
}

function formatNumber(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)}K`;
  return n.toLocaleString();
}

/**
 * KPI sidecar — bento dashboard layout.
 *
 * Renders as a 2-column grid that occupies the right 4-column track of
 * the dashboard bento. Two stat cards on top (Tokens, Cost), one wide
 * health summary card on the bottom spanning both columns. Heights are
 * driven by the parent grid so the sidecar visually matches the
 * adjacent welcome banner.
 */
export function MetricsGrid({
  totalTokens = 0,
  estimatedCost = 0,
  weekChangePct,
}: MetricsGridProps) {
  const t = useTranslations("dashboard");

  const formattedCost =
    typeof estimatedCost === "number"
      ? `$${estimatedCost.toFixed(2)}`
      : `$${estimatedCost}`;

  return (
    <div className="grid h-full grid-cols-2 gap-4">
      <StatCard
        icon={<IconCoin className="size-4 text-[rgb(var(--color-primary))]" />}
        label={t("metricsTokens")}
        value={formatNumber(totalTokens)}
        trend={
          typeof weekChangePct === "number"
            ? {
                value: `${weekChangePct >= 0 ? "+" : ""}${weekChangePct}%`,
                hint: t("metricsTokensHint"),
                positive: weekChangePct >= 0,
              }
            : { value: "", hint: t("metricsTokensHint"), positive: true }
        }
      />
      <StatCard
        icon={<IconCash className="size-4 text-[rgb(var(--color-primary))]" />}
        label={t("metricsCost")}
        value={formattedCost}
        hint={t("metricsCostHint")}
      />
      <HealthCard
        label={t("metricsHealth")}
        value={t("metricsHealthOk")}
        responseLabel={t("metricsResponseTime")}
        responseValue={t("metricsResponseValue")}
      />
    </div>
  );
}

function StatCard({
  icon,
  label,
  value,
  hint,
  trend,
}: {
  icon: React.ReactNode;
  label: string;
  value: string;
  hint?: string;
  trend?: { value: string; hint: string; positive: boolean };
}) {
  return (
    <div className="col-span-1 flex flex-col justify-center rounded-xl border sh-card p-4">
      <div className="mb-2 flex items-center justify-between">
        <span className="text-[11px] font-semibold uppercase tracking-wider sh-muted">
          {label}
        </span>
        <span aria-hidden>{icon}</span>
      </div>
      <div className="text-[28px] font-semibold leading-[34px] tracking-tight">
        {value}
      </div>
      {trend ? (
        <div className="mt-1 flex items-center gap-1 text-[11px] sh-muted">
          {trend.value && (
            <>
              <IconTrendingUp
                className={
                  trend.positive
                    ? "size-3.5 text-emerald-600"
                    : "size-3.5 text-red-600"
                }
              />
              <span
                className={
                  trend.positive ? "text-emerald-600" : "text-red-600"
                }
              >
                {trend.value}
              </span>
            </>
          )}
          <span>{trend.hint}</span>
        </div>
      ) : hint ? (
        <div className="mt-1 text-[11px] sh-muted">{hint}</div>
      ) : null}
    </div>
  );
}

function HealthCard({
  label,
  value,
  responseLabel,
  responseValue,
}: {
  label: string;
  value: string;
  responseLabel: string;
  responseValue: string;
}) {
  return (
    <div className="col-span-2 flex items-center justify-between rounded-xl border sh-card p-4">
      <div>
        <div className="mb-1 flex items-center gap-1.5 text-[11px] font-semibold uppercase tracking-wider sh-muted">
          <IconActivity className="size-3.5" />
          {label}
        </div>
        <div className="flex items-center gap-2 text-[18px] font-semibold tracking-tight">
          <span className="size-2.5 rounded-full bg-emerald-500" aria-hidden />
          {value}
        </div>
      </div>
      <div className="text-right">
        <div className="mb-1 text-[11px] font-semibold uppercase tracking-wider sh-muted">
          {responseLabel}
        </div>
        <div className="text-[14px] font-medium">{responseValue}</div>
      </div>
    </div>
  );
}
