"use client";

/**
 * UsageEventsTable — read-only audit of LLM / budget-tracked usage.
 *
 * Backend: `GET /api/v1/governance/usage-events`. Filtering is done
 * client-side (server returns the most recent 200 rows by default); the
 * filters cover the high-signal cases admins actually sort by.
 */

import { useMemo, useState } from "react";
import { useLocale, useTranslations } from "next-intl";
import { IconRefresh, IconActivity } from "@tabler/icons-react";

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
import { useUsageEvents } from "@/hooks/use-governance";
import { relativeTime } from "@/lib/utils";

export function UsageEventsTable() {
  const t = useTranslations("settings.governance");
  const locale = useLocale();
  const { data, isLoading, isFetching, refetch } = useUsageEvents({ limit: 200 });

  const [eventType, setEventType] = useState<string>("all");
  const [provider, setProvider] = useState<string>("all");
  const [q, setQ] = useState("");

  const { eventTypes, providers, filtered, totalCost, totalTokens } = useMemo(() => {
    const rows = data ?? [];
    const evs = new Set<string>();
    const provs = new Set<string>();
    for (const r of rows) {
      evs.add(r.event_type);
      if (r.provider) provs.add(r.provider);
    }
    let totalCost = 0;
    let totalTokens = 0;
    const needle = q.trim().toLowerCase();
    const filtered = rows.filter((r) => {
      if (eventType !== "all" && r.event_type !== eventType) return false;
      if (provider !== "all" && r.provider !== provider) return false;
      if (needle) {
        const hay = `${r.model ?? ""} ${r.tool_name ?? ""} ${r.event_type}`.toLowerCase();
        if (!hay.includes(needle)) return false;
      }
      totalCost += Number(r.cost_usd ?? 0);
      totalTokens += (r.input_tokens ?? 0) + (r.output_tokens ?? 0);
      return true;
    });
    return {
      eventTypes: Array.from(evs).sort(),
      providers: Array.from(provs).sort(),
      filtered,
      totalCost,
      totalTokens,
    };
  }, [data, eventType, provider, q]);

  return (
    <div>
      <div className="mb-3 flex flex-wrap items-center gap-2">
        <Input
          value={q}
          onChange={(e) => setQ(e.target.value)}
          placeholder={t("usage.searchPlaceholder")}
          className="max-w-[240px]"
        />
        <Select value={eventType} onValueChange={setEventType}>
          <SelectTrigger className="w-[140px]">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="all">{t("usage.allEvents")}</SelectItem>
            {eventTypes.map((e) => (
              <SelectItem key={e} value={e}>
                {e}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
        <Select value={provider} onValueChange={setProvider}>
          <SelectTrigger className="w-[140px]">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="all">{t("usage.allProviders")}</SelectItem>
            {providers.map((p) => (
              <SelectItem key={p} value={p}>
                {p}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
        <Button variant="ghost" size="sm" onClick={() => void refetch()} disabled={isFetching}>
          <IconRefresh className="size-4" />
        </Button>
        <div className="ml-auto flex items-center gap-3 text-[11px] sh-muted">
          <span>
            {t("usage.totals", {
              rows: filtered.length,
              tokens: totalTokens.toLocaleString(locale),
              cost: totalCost.toFixed(4),
            })}
          </span>
        </div>
      </div>

      {isLoading && <Skeleton className="h-32" />}

      {!isLoading && filtered.length === 0 && (
        <Card>
          <CardContent className="py-10 text-center">
            <IconActivity className="mx-auto size-8 sh-muted" />
            <p className="mt-3 text-sm sh-muted">{t("usage.empty")}</p>
          </CardContent>
        </Card>
      )}

      {filtered.length > 0 && (
        <Card>
          <CardContent className="overflow-x-auto p-0">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b text-[11px] uppercase sh-muted">
                  <th className="px-3 py-1.5 text-left font-medium">{t("usage.col.time")}</th>
                  <th className="px-3 py-1.5 text-left font-medium">{t("usage.col.event")}</th>
                  <th className="px-3 py-1.5 text-left font-medium">{t("usage.col.provider")}</th>
                  <th className="px-3 py-1.5 text-left font-medium">{t("usage.col.model")}</th>
                  <th className="px-3 py-1.5 text-right font-medium">{t("usage.col.tokens")}</th>
                  <th className="px-3 py-1.5 text-right font-medium">{t("usage.col.cost")}</th>
                  <th className="px-3 py-1.5 text-left font-medium">{t("usage.col.tool")}</th>
                </tr>
              </thead>
              <tbody>
                {filtered.map((r) => {
                  const tokens =
                    (r.input_tokens ?? 0) + (r.output_tokens ?? 0);
                  return (
                    <tr key={r.id} className="border-b last:border-b-0">
                      <td className="px-3 py-1.5 text-[11px] sh-muted">
                        {relativeTime(r.created_at, locale)}
                      </td>
                      <td className="px-3 py-1.5">
                        <Badge variant="outline">{r.event_type}</Badge>
                      </td>
                      <td className="px-3 py-1.5 font-mono text-[12px]">
                        {r.provider ?? "—"}
                      </td>
                      <td className="px-3 py-1.5 font-mono text-[12px]">
                        {r.model ?? "—"}
                      </td>
                      <td className="px-3 py-1.5 text-right tabular-nums">
                        {tokens.toLocaleString(locale)}
                      </td>
                      <td className="px-3 py-1.5 text-right tabular-nums">
                        {r.cost_usd ? `$${Number(r.cost_usd).toFixed(4)}` : "—"}
                      </td>
                      <td className="px-3 py-1.5 font-mono text-[12px]">
                        {r.tool_name ?? "—"}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </CardContent>
        </Card>
      )}
    </div>
  );
}
