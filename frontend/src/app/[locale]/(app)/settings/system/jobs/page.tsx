"use client";

import { useMemo, useState } from "react";
import { IconRefresh } from "@tabler/icons-react";
import { useLocale, useTranslations } from "next-intl";
import { toast } from "sonner";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { PageHeader } from "@/components/ui/page-header";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";
import { useMe } from "@/hooks/use-me";
import {
  type JobRunRow,
  type JobRunStatus,
  useJobHealth,
  useJobQueues,
  useRecentJobs,
  useRetryJob,
} from "@/hooks/use-admin-jobs";
import { relativeTime } from "@/lib/utils";

const STATUS_VALUES: readonly JobRunStatus[] = [
  "queued",
  "running",
  "success",
  "failed",
  "failed_permanent",
] as const;

const STATUS_VARIANT: Record<
  JobRunStatus,
  "default" | "outline" | "primary" | "success" | "warning" | "danger"
> = {
  queued: "outline",
  running: "primary",
  success: "success",
  failed: "warning",
  failed_permanent: "danger",
};

function formatDuration(ms: number | null | undefined): string {
  if (ms === null || ms === undefined) return "—";
  if (ms < 1000) return `${ms}ms`;
  if (ms < 60_000) return `${(ms / 1000).toFixed(1)}s`;
  const min = Math.floor(ms / 60_000);
  const sec = Math.floor((ms % 60_000) / 1000);
  return `${min}m${sec}s`;
}

export default function BackgroundJobsPage() {
  const t = useTranslations("adminJobs");
  const tCommon = useTranslations("common");
  const locale = useLocale();
  const { data: me } = useMe();
  const isPlatformAdmin = me?.platform_role === "platform_admin";

  const [statusFilter, setStatusFilter] = useState<JobRunStatus | "__ALL__">(
    "__ALL__",
  );
  const [functionFilter, setFunctionFilter] = useState("");

  const queueStats = useJobQueues();
  const healthQuery = useJobHealth();
  const recent = useRecentJobs({
    status: statusFilter === "__ALL__" ? "" : statusFilter,
    function_name: functionFilter || undefined,
    limit: 200,
  });
  const retryMut = useRetryJob();

  const onRetry = async (row: JobRunRow) => {
    try {
      const res = await retryMut.mutateAsync(row.job_id);
      if (res.enqueued) {
        toast.success(t("retrySuccess", { jobId: res.new_job_id ?? "—" }));
      } else {
        toast.error(t("retryQueueOffline"));
      }
    } catch {
      toast.error(t("retryFailed"));
    }
  };

  const totals = healthQuery.data?.totals;

  const functionOptions = useMemo(
    () =>
      (queueStats.data?.by_function ?? [])
        .map((r) => r.function_name)
        .sort(),
    [queueStats.data],
  );

  const refreshAll = () => {
    queueStats.refetch();
    healthQuery.refetch();
    recent.refetch();
  };

  const isFetching =
    queueStats.isFetching || healthQuery.isFetching || recent.isFetching;

  return (
    <div className="space-y-6">
      <PageHeader
        title={t("title")}
        description={t("description")}
        actions={
          <Button
            variant="outline"
            size="sm"
            onClick={refreshAll}
            disabled={isFetching}
            title={tCommon("refresh")}
          >
            <IconRefresh
              className={`size-4 ${isFetching ? "animate-spin" : ""}`}
            />
          </Button>
        }
      />

      {/* Headline counters */}
      <section className="grid gap-3 sm:grid-cols-3 lg:grid-cols-6">
        {(
          [
            { label: t("stat.queued"), value: totals?.queued ?? 0, variant: "outline" as const },
            { label: t("stat.running"), value: totals?.running ?? 0, variant: "primary" as const },
            { label: t("stat.success"), value: totals?.success ?? 0, variant: "success" as const },
            { label: t("stat.failed"), value: totals?.failed ?? 0, variant: "warning" as const },
            {
              label: t("stat.failedPermanent"),
              value: totals?.failed_permanent ?? 0,
              variant: "danger" as const,
            },
            {
              label: t("stat.failedPermanentTotal"),
              value: totals?.failed_permanent_total ?? 0,
              variant: "danger" as const,
            },
          ]
        ).map((card) => (
          <Card key={card.label}>
            <CardContent className="py-3">
              <div className="text-[11px] sh-muted">{card.label}</div>
              <div className="mt-1 flex items-baseline gap-2">
                <span className="text-2xl font-semibold tabular-nums">
                  {card.value}
                </span>
                <Badge variant={card.variant} className="text-[10px]">
                  {tCommon("note")}
                </Badge>
              </div>
            </CardContent>
          </Card>
        ))}
      </section>

      {/* Per-function queue depths */}
      <Card>
        <CardHeader>
          <CardTitle className="text-base">
            {t("byFunctionTitle")}
          </CardTitle>
          <CardDescription>
            {t("byFunctionDesc", {
              redis:
                queueStats.data?.redis_queue.depth === null
                  ? t("redisOffline")
                  : String(queueStats.data?.redis_queue.depth ?? 0),
              queueName:
                queueStats.data?.redis_queue.queue_name ?? "arq:queue",
            })}
          </CardDescription>
        </CardHeader>
        <CardContent>
          {queueStats.isLoading ? (
            <Skeleton className="h-32" />
          ) : !queueStats.data?.by_function.length ? (
            <p className="py-6 text-center text-xs sh-muted">
              {t("emptyFunctions")}
            </p>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead>
                  <tr className="border-b text-[11px] uppercase sh-muted">
                    <th className="py-2 text-left font-medium">
                      {t("col.function")}
                    </th>
                    <th className="py-2 text-right font-medium">
                      {t("col.queued")}
                    </th>
                    <th className="py-2 text-right font-medium">
                      {t("col.running")}
                    </th>
                    <th className="py-2 text-right font-medium">
                      {t("col.success1h")}
                    </th>
                    <th className="py-2 text-right font-medium">
                      {t("col.failed1h")}
                    </th>
                    <th className="py-2 text-right font-medium">
                      {t("col.failedPerm1h")}
                    </th>
                  </tr>
                </thead>
                <tbody>
                  {queueStats.data.by_function.map((row) => (
                    <tr
                      key={row.function_name}
                      className="border-b last:border-b-0 align-top"
                    >
                      <td className="py-1.5 font-mono text-[12px]">
                        {row.function_name}
                      </td>
                      <td className="py-1.5 text-right tabular-nums">
                        {row.queued}
                      </td>
                      <td className="py-1.5 text-right tabular-nums">
                        {row.running}
                      </td>
                      <td className="py-1.5 text-right tabular-nums">
                        {row.success}
                      </td>
                      <td className="py-1.5 text-right tabular-nums">
                        {row.failed}
                      </td>
                      <td className="py-1.5 text-right tabular-nums">
                        {row.failed_permanent}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </CardContent>
      </Card>

      {/* Recent runs */}
      <Card>
        <CardHeader>
          <CardTitle className="text-base">{t("recentTitle")}</CardTitle>
          <CardDescription>{t("recentDesc")}</CardDescription>
        </CardHeader>
        <CardContent>
          <div className="mb-3 grid gap-3 sm:grid-cols-3">
            <div>
              <label className="text-[11px] sh-muted">
                {t("filter.status")}
              </label>
              <Select
                value={statusFilter}
                onValueChange={(v) =>
                  setStatusFilter(v as JobRunStatus | "__ALL__")
                }
              >
                <SelectTrigger>
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="__ALL__">{t("filter.statusAny")}</SelectItem>
                  {STATUS_VALUES.map((s) => (
                    <SelectItem key={s} value={s}>
                      {t(`status.${s}`)}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
            <div>
              <label className="text-[11px] sh-muted">
                {t("filter.function")}
              </label>
              <Select
                value={functionFilter || "__ALL__"}
                onValueChange={(v) =>
                  setFunctionFilter(v === "__ALL__" ? "" : v)
                }
              >
                <SelectTrigger>
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="__ALL__">{t("filter.functionAny")}</SelectItem>
                  {functionOptions.map((fn) => (
                    <SelectItem key={fn} value={fn}>
                      {fn}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
            <div className="hidden sm:block">
              <label className="text-[11px] sh-muted">
                {t("filter.search")}
              </label>
              <Input
                value={functionFilter}
                onChange={(e) => setFunctionFilter(e.target.value)}
                placeholder={t("filter.searchPlaceholder")}
              />
            </div>
          </div>

          {recent.isLoading ? (
            <Skeleton className="h-60" />
          ) : !recent.data?.length ? (
            <p className="py-6 text-center text-xs sh-muted">
              {t("emptyRecent")}
            </p>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead>
                  <tr className="border-b text-[11px] uppercase sh-muted">
                    <th className="py-2 text-left font-medium">
                      {t("col.function")}
                    </th>
                    <th className="py-2 text-left font-medium">
                      {t("col.statusLabel")}
                    </th>
                    <th className="py-2 text-left font-medium">
                      {t("col.workspace")}
                    </th>
                    <th className="py-2 text-right font-medium">
                      {t("col.duration")}
                    </th>
                    <th className="py-2 text-right font-medium">
                      {t("col.retry")}
                    </th>
                    <th className="py-2 text-left font-medium">
                      {t("col.enqueued")}
                    </th>
                    <th className="py-2 text-left font-medium">
                      {t("col.finished")}
                    </th>
                    <th className="py-2 text-left font-medium">
                      {t("col.error")}
                    </th>
                    <th className="py-2 text-right font-medium">
                      {t("col.actions")}
                    </th>
                  </tr>
                </thead>
                <tbody>
                  {recent.data.map((row) => (
                    <tr
                      key={row.id}
                      className="border-b last:border-b-0 align-top"
                    >
                      <td className="py-1.5 font-mono text-[11px]">
                        {row.function_name}
                      </td>
                      <td className="py-1.5">
                        <Badge variant={STATUS_VARIANT[row.status]}>
                          {t(`status.${row.status}`)}
                        </Badge>
                      </td>
                      <td className="py-1.5 font-mono text-[11px] sh-muted">
                        {row.workspace_id ? row.workspace_id.slice(0, 8) : "—"}
                      </td>
                      <td className="py-1.5 text-right tabular-nums">
                        {formatDuration(row.duration_ms)}
                      </td>
                      <td className="py-1.5 text-right tabular-nums">
                        {row.retry_count}
                      </td>
                      <td
                        className="py-1.5 text-[11px] sh-muted"
                        title={new Date(row.enqueued_at).toLocaleString(locale)}
                      >
                        {relativeTime(row.enqueued_at, locale)}
                      </td>
                      <td
                        className="py-1.5 text-[11px] sh-muted"
                        title={
                          row.finished_at
                            ? new Date(row.finished_at).toLocaleString(locale)
                            : ""
                        }
                      >
                        {row.finished_at
                          ? relativeTime(row.finished_at, locale)
                          : "—"}
                      </td>
                      <td className="py-1.5">
                        {row.error_class ? (
                          <details>
                            <summary className="cursor-pointer text-[11px] sh-muted">
                              {row.error_class}
                            </summary>
                            <pre className="mt-1 max-w-md overflow-x-auto rounded bg-black/5 p-1.5 text-[10px] dark:bg-white/5">
                              {row.error_message ?? ""}
                            </pre>
                          </details>
                        ) : (
                          <span className="sh-muted">—</span>
                        )}
                      </td>
                      <td className="py-1.5 text-right">
                        {isPlatformAdmin &&
                        (row.status === "failed_permanent" ||
                          row.status === "failed") ? (
                          <Button
                            variant="outline"
                            size="sm"
                            disabled={retryMut.isPending}
                            onClick={() => onRetry(row)}
                          >
                            {t("retry")}
                          </Button>
                        ) : null}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
