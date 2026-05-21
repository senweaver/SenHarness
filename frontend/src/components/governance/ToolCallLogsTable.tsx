"use client";

/**
 * ToolCallLogsTable — read-only audit of tool invocations.
 *
 * Backend: `GET /api/v1/governance/tool-call-logs`. Filters run client
 * side (same reasoning as UsageEventsTable). The `status` column is the
 * high-signal one — admins scan for `error` to find tools that keep
 * failing on a given Agent.
 */

import { useMemo, useState } from "react";
import { useLocale, useTranslations } from "next-intl";
import { IconRefresh, IconTool } from "@tabler/icons-react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";
import { useToolCallLogs } from "@/hooks/use-governance";
import { relativeTime } from "@/lib/utils";

function statusVariant(
  s: string,
): "success" | "warning" | "danger" | "default" {
  if (s === "success" || s === "ok") return "success";
  if (s === "error" || s === "failed" || s === "fail") return "danger";
  if (s === "denied" || s === "blocked") return "warning";
  return "default";
}

export function ToolCallLogsTable() {
  const t = useTranslations("settings.governance");
  const locale = useLocale();
  const { data, isLoading, isFetching, refetch } = useToolCallLogs({ limit: 200 });

  const [status, setStatus] = useState<string>("all");
  const [q, setQ] = useState("");

  const { statuses, filtered, errorCount, successCount } = useMemo(() => {
    const rows = data ?? [];
    const sts = new Set<string>();
    for (const r of rows) sts.add(r.status);
    const needle = q.trim().toLowerCase();
    let errorCount = 0;
    let successCount = 0;
    const filtered = rows.filter((r) => {
      if (status !== "all" && r.status !== status) return false;
      if (needle) {
        const hay = `${r.tool_name} ${r.error_text ?? ""}`.toLowerCase();
        if (!hay.includes(needle)) return false;
      }
      if (r.status === "success" || r.status === "ok") successCount += 1;
      else errorCount += 1;
      return true;
    });
    return {
      statuses: Array.from(sts).sort(),
      filtered,
      errorCount,
      successCount,
    };
  }, [data, status, q]);

  return (
    <div>
      <div className="mb-3 flex flex-wrap items-center gap-2">
        <Input
          value={q}
          onChange={(e) => setQ(e.target.value)}
          placeholder={t("tool.searchPlaceholder")}
          className="max-w-[240px]"
        />
        <Select value={status} onValueChange={setStatus}>
          <SelectTrigger className="w-[140px]">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="all">{t("tool.allStatuses")}</SelectItem>
            {statuses.map((s) => (
              <SelectItem key={s} value={s}>
                {s}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
        <Button variant="ghost" size="sm" onClick={() => void refetch()} disabled={isFetching}>
          <IconRefresh className="size-4" />
        </Button>
        <div className="ml-auto text-[11px] sh-muted">
          {t("tool.totals", { rows: filtered.length, ok: successCount, err: errorCount })}
        </div>
      </div>

      {isLoading && <Skeleton className="h-32" />}

      {!isLoading && filtered.length === 0 && (
        <Card>
          <CardContent className="py-10 text-center">
            <IconTool className="mx-auto size-8 sh-muted" />
            <p className="mt-3 text-sm sh-muted">{t("tool.empty")}</p>
          </CardContent>
        </Card>
      )}

      {filtered.length > 0 && (
        <Card>
          <CardContent className="overflow-x-auto p-0">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b text-[11px] uppercase sh-muted">
                  <th className="px-3 py-1.5 text-left font-medium">{t("tool.col.time")}</th>
                  <th className="px-3 py-1.5 text-left font-medium">{t("tool.col.tool")}</th>
                  <th className="px-3 py-1.5 text-left font-medium">{t("tool.col.status")}</th>
                  <th className="px-3 py-1.5 text-right font-medium">{t("tool.col.duration")}</th>
                  <th className="px-3 py-1.5 text-right font-medium">{t("tool.col.cost")}</th>
                  <th className="px-3 py-1.5 text-left font-medium">{t("tool.col.error")}</th>
                </tr>
              </thead>
              <tbody>
                {filtered.map((r) => (
                  <tr key={r.id} className="border-b last:border-b-0">
                    <td className="px-3 py-1.5 text-[11px] sh-muted">
                      {relativeTime(r.created_at, locale)}
                    </td>
                    <td className="px-3 py-1.5 font-mono text-[12px]">
                      {r.tool_name}
                    </td>
                    <td className="px-3 py-1.5">
                      <Badge variant={statusVariant(r.status)}>{r.status}</Badge>
                    </td>
                    <td className="px-3 py-1.5 text-right tabular-nums">
                      {r.duration_ms ? `${r.duration_ms}ms` : "—"}
                    </td>
                    <td className="px-3 py-1.5 text-right tabular-nums">
                      {r.cost_usd ? `$${Number(r.cost_usd).toFixed(4)}` : "—"}
                    </td>
                    <td className="px-3 py-1.5 truncate text-[12px] text-destructive">
                      {r.error_text ?? ""}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </CardContent>
        </Card>
      )}
    </div>
  );
}
