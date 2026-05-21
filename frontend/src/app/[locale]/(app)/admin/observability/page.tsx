"use client";

import { useMemo } from "react";
import { useTranslations } from "next-intl";
import {
  IconActivity,
  IconAlertTriangle,
  IconChartBar,
  IconCoin,
  IconGauge,
  IconRefresh,
  IconServer2,
  IconTool,
} from "@tabler/icons-react";

import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import { PageHeader } from "@/components/ui/page-header";
import { Button } from "@/components/ui/button";
import {
  histogramPercentile,
  sumCounter,
  usePrometheusSnapshot,
  type CounterMetric,
  type HistogramMetric,
} from "@/hooks/use-observability";

export default function ObservabilityPage() {
  const t = useTranslations("admin.observability");
  const { data, isLoading, isError, refetch, isFetching } =
    usePrometheusSnapshot();

  const runs = data?.counters["senharness_agent_runs_total"];
  const tokens = data?.counters["senharness_agent_tokens_total"];
  const cost = data?.counters["senharness_agent_cost_usd_total"];
  const toolCalls = data?.counters["senharness_tool_calls_total"];
  const evals = data?.counters["senharness_eval_verdict_total"];
  const http = data?.counters["senharness_http_requests_total"];
  const runLatency = data?.histograms["senharness_agent_run_duration_seconds"];
  const httpLatency =
    data?.histograms["senharness_http_request_duration_seconds"];

  const totalRuns = sumCounter(runs);
  const failedRuns = sumCounter(
    runs,
    (l) => Boolean(l.status && l.status !== "ok"),
  );
  const totalCost = sumCounter(cost);
  const totalInput = sumCounter(
    tokens,
    (l) => l.direction === "input",
  );
  const totalOutput = sumCounter(
    tokens,
    (l) => l.direction === "output",
  );
  const totalToolCalls = sumCounter(toolCalls);
  const failedTools = sumCounter(toolCalls, (l) => l.status !== "ok");

  const runP95 = useMemo(() => hPercentile(runLatency, 0.95), [runLatency]);
  const runP50 = useMemo(() => hPercentile(runLatency, 0.5), [runLatency]);
  const httpP95 = useMemo(() => hPercentile(httpLatency, 0.95), [httpLatency]);

  return (
    <div className="space-y-4">
      <PageHeader
        title={t("title")}
        description={t("description")}
        actions={
          <Button
            size="sm"
            variant="outline"
            onClick={() => refetch()}
            disabled={isFetching}
          >
            <IconRefresh className={isFetching ? "size-4 animate-spin" : "size-4"} />
            {t("refresh")}
          </Button>
        }
      />

      {isError && (
        <Card>
          <CardContent className="flex items-center gap-2 py-10 text-sm sh-muted">
            <IconAlertTriangle className="size-4 text-amber-500" />
            {t("scrapeFailed")}
          </CardContent>
        </Card>
      )}

      {isLoading && !data && <Skeleton className="h-24" />}

      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        <Kpi
          icon={<IconGauge className="size-4 text-[rgb(var(--color-primary))]" />}
          label={t("kpi.totalRuns")}
          value={totalRuns.toLocaleString()}
          hint={
            totalRuns > 0
              ? `${Math.round((1 - failedRuns / totalRuns) * 100)}% ${t("kpi.success")}`
              : undefined
          }
        />
        <Kpi
          icon={<IconChartBar className="size-4" />}
          label={t("kpi.tokens")}
          value={`${totalInput.toLocaleString()} → ${totalOutput.toLocaleString()}`}
          hint={t("kpi.tokensHint")}
        />
        <Kpi
          icon={<IconCoin className="size-4 text-amber-500" />}
          label={t("kpi.cost")}
          value={`$${totalCost.toFixed(4)}`}
        />
        <Kpi
          icon={<IconActivity className="size-4" />}
          label={t("kpi.p95")}
          value={runP95 !== null ? `${runP95.toFixed(2)}s` : "—"}
          hint={
            runP50 !== null ? `p50 ${runP50.toFixed(2)}s` : undefined
          }
        />
      </div>

      <div className="grid gap-3 lg:grid-cols-2">
        <BreakdownCard
          title={t("breakdown.runsByModel")}
          description={t("breakdown.runsByModelDesc")}
          icon={<IconServer2 className="size-4" />}
          counter={runs}
          labelKeys={["model", "provider"]}
          statusKey="status"
        />
        <BreakdownCard
          title={t("breakdown.tools")}
          description={t("breakdown.toolsDesc")}
          icon={<IconTool className="size-4" />}
          counter={toolCalls}
          labelKeys={["tool"]}
          statusKey="status"
          extra={
            <div className="text-[11px] sh-muted">
              {totalToolCalls.toLocaleString()} {t("breakdown.totalCalls")} ·{" "}
              {failedTools.toLocaleString()} {t("breakdown.failed")}
            </div>
          }
        />
        <BreakdownCard
          title={t("breakdown.evals")}
          description={t("breakdown.evalsDesc")}
          icon={<IconActivity className="size-4" />}
          counter={evals}
          labelKeys={["verdict"]}
        />
        <HttpCard
          title={t("breakdown.http")}
          counter={http}
          p95={httpP95}
        />
      </div>

      {data && (
        <details className="mt-2 text-xs">
          <summary className="cursor-pointer sh-muted hover:underline">
            {t("rawExposition")}
          </summary>
          <pre className="mt-2 max-h-80 overflow-y-auto rounded border bg-black/5 p-3 text-[11px] leading-relaxed dark:bg-white/5">
            {data.raw}
          </pre>
        </details>
      )}
    </div>
  );
}

function hPercentile(h: HistogramMetric | undefined, p: number): number | null {
  if (!h) return null;
  // Aggregate across every series into a synthetic one.
  const merged: Record<string, number> = {};
  let sum = 0;
  let count = 0;
  for (const s of h.series) {
    sum += s.sum;
    count += s.count;
    for (const b of s.buckets) {
      merged[b.le] = (merged[b.le] ?? 0) + b.count;
    }
  }
  if (count === 0) return null;
  return histogramPercentile(
    {
      labels: {},
      buckets: Object.entries(merged)
        .map(([le, c]) => ({ le, count: c }))
        .sort((a, b) =>
          (a.le === "+Inf" ? Infinity : Number(a.le)) -
          (b.le === "+Inf" ? Infinity : Number(b.le)),
        ),
      sum,
      count,
    },
    p,
  );
}

function Kpi({
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
      <CardContent className="py-4">
        <div className="mb-1 flex items-center gap-2 text-[11px] uppercase sh-muted">
          {icon}
          {label}
        </div>
        <div className="font-mono text-xl">{value}</div>
        {hint && <div className="mt-1 text-[11px] sh-muted">{hint}</div>}
      </CardContent>
    </Card>
  );
}

function BreakdownCard({
  title,
  description,
  icon,
  counter,
  labelKeys,
  statusKey,
  extra,
}: {
  title: string;
  description?: string;
  icon: React.ReactNode;
  counter?: CounterMetric;
  labelKeys: string[];
  statusKey?: string;
  extra?: React.ReactNode;
}) {
  const t = useTranslations("admin.observability");
  // Aggregate by selected label keys; status label becomes a variant hint.
  const rows = useMemo(() => {
    if (!counter) return [] as Array<{ key: string; value: number; status?: string }>;
    const byKey: Record<string, { value: number; statuses: Record<string, number> }> = {};
    for (const s of counter.samples) {
      const key = labelKeys
        .map((k) => s.labels[k] ?? "—")
        .join(" · ");
      const status = statusKey ? s.labels[statusKey] : undefined;
      byKey[key] ??= { value: 0, statuses: {} };
      byKey[key].value += s.value;
      if (status) byKey[key].statuses[status] = (byKey[key].statuses[status] ?? 0) + s.value;
    }
    return Object.entries(byKey)
      .map(([key, v]) => {
        const worstStatus = Object.entries(v.statuses).sort(
          (a, b) => b[1] - a[1],
        )[0]?.[0];
        return { key, value: v.value, status: worstStatus };
      })
      .sort((a, b) => b.value - a.value)
      .slice(0, 10);
  }, [counter, labelKeys, statusKey]);

  const max = rows[0]?.value ?? 1;

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2 text-sm">
          {icon}
          {title}
        </CardTitle>
        {description && <CardDescription>{description}</CardDescription>}
      </CardHeader>
      <CardContent>
        {extra}
        {rows.length === 0 && (
          <p className="text-sm sh-muted">{t("noData")}</p>
        )}
        <ul className="space-y-1">
          {rows.map((r) => (
            <li key={r.key} className="flex items-center gap-2">
              <span className="flex-1 truncate font-mono text-[11px]">{r.key}</span>
              {r.status && (
                <Badge
                  variant={
                    r.status === "ok" || r.status === "pass"
                      ? "success"
                      : r.status === "warn"
                        ? "warning"
                        : r.status === "fail"
                          ? "danger"
                          : "outline"
                  }
                >
                  {r.status}
                </Badge>
              )}
              <div className="h-1.5 w-32 overflow-hidden rounded-full bg-black/5 dark:bg-white/10">
                <div
                  className="h-full bg-[rgb(var(--color-primary))]"
                  style={{ width: `${Math.round((r.value / max) * 100)}%` }}
                />
              </div>
              <span className="w-14 text-right font-mono text-[11px]">
                {r.value.toLocaleString()}
              </span>
            </li>
          ))}
        </ul>
      </CardContent>
    </Card>
  );
}

function HttpCard({
  title,
  counter,
  p95,
}: {
  title: string;
  counter?: CounterMetric;
  p95: number | null;
}) {
  const t = useTranslations("admin.observability");
  const total = sumCounter(counter);
  const err5xx = sumCounter(counter, (l) => Number(l.status) >= 500);
  const err4xx = sumCounter(
    counter,
    (l) => Number(l.status) >= 400 && Number(l.status) < 500,
  );

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2 text-sm">
          <IconActivity className="size-4" />
          {title}
        </CardTitle>
      </CardHeader>
      <CardContent className="space-y-2 text-xs">
        <div className="flex items-center gap-4">
          <Stat label={t("breakdown.totalRequests")} value={total.toLocaleString()} />
          <Stat label="5xx" value={err5xx.toLocaleString()} tone="danger" />
          <Stat label="4xx" value={err4xx.toLocaleString()} tone="warning" />
          <Stat
            label={t("breakdown.httpP95")}
            value={p95 !== null ? `${(p95 * 1000).toFixed(0)}ms` : "—"}
          />
        </div>
      </CardContent>
    </Card>
  );
}

function Stat({
  label,
  value,
  tone,
}: {
  label: string;
  value: string;
  tone?: "danger" | "warning";
}) {
  return (
    <div className="flex flex-col">
      <span
        className={
          "text-[10px] uppercase sh-muted " +
          (tone === "danger"
            ? "text-red-600/80"
            : tone === "warning"
              ? "text-amber-600/80"
              : "")
        }
      >
        {label}
      </span>
      <span className="font-mono text-[13px]">{value}</span>
    </div>
  );
}
