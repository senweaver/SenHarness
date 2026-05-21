"use client";

import { useMemo, useState } from "react";
import { useLocale, useTranslations } from "next-intl";
import {
  IconBolt,
  IconCash,
  IconClock,
  IconMessage2,
} from "@tabler/icons-react";

import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { PageHeader, SectionHeader } from "@/components/ui/page-header";
import { Skeleton } from "@/components/ui/skeleton";
import { SparkBar } from "@/components/usage/SparkBar";
import { useUsageReport } from "@/hooks/use-usage";

type WindowKey = "7d" | "30d" | "90d";
type ScopeKey = "auto" | "me" | "workspace";

export default function UsagePage() {
  const t = useTranslations("settings.usage");
  const locale = useLocale();
  const [winKey, setWinKey] = useState<WindowKey>("30d");
  const [scope, setScope] = useState<ScopeKey>("auto");

  const { since, until } = useMemo(() => toRange(winKey), [winKey]);
  const { data, isLoading } = useUsageReport({ since, until, scope });

  const usd = (n: number) =>
    `$${n >= 1 ? n.toFixed(2) : n >= 0.01 ? n.toFixed(4) : n.toFixed(6)}`;
  const num = (n: number) => n.toLocaleString(locale);
  const ms = (n: number) =>
    n >= 1000 ? `${(n / 1000).toFixed(1)}s` : `${Math.round(n)}ms`;

  const daily = data?.daily ?? [];
  const bars = useMemo(
    () =>
      daily.map((d) => ({
        label: d.date,
        value: d.cost_usd,
        tooltip: `${d.date}: ${usd(d.cost_usd)} · ${num(
          d.input_tokens + d.output_tokens,
        )} tokens · ${num(d.turns)} turns`,
      })),
    [daily],
  );
  const tokenBars = useMemo(
    () =>
      daily.map((d) => ({
        label: d.date,
        value: d.input_tokens + d.output_tokens,
        tooltip: `${d.date}: ${num(d.input_tokens + d.output_tokens)} tokens`,
      })),
    [daily],
  );

  return (
    <div>
      <PageHeader
        title={t("title")}
        description={t("description")}
        actions={
          <div className="flex items-center gap-2">
            <Select
              value={scope}
              onValueChange={(v) => setScope(v as ScopeKey)}
            >
              <SelectTrigger className="w-[150px]">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="auto">{t("scope.auto")}</SelectItem>
                <SelectItem value="me">{t("scope.me")}</SelectItem>
                <SelectItem value="workspace">
                  {t("scope.workspace")}
                </SelectItem>
              </SelectContent>
            </Select>
            <Select
              value={winKey}
              onValueChange={(v) => setWinKey(v as WindowKey)}
            >
              <SelectTrigger className="w-[120px]">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="7d">{t("window.7d")}</SelectItem>
                <SelectItem value="30d">{t("window.30d")}</SelectItem>
                <SelectItem value="90d">{t("window.90d")}</SelectItem>
              </SelectContent>
            </Select>
          </div>
        }
      />

      {data?.scope === "me" && scope !== "me" && (
        <div className="mb-3 rounded-md border border-dashed bg-black/2 p-2 text-xs sh-muted dark:bg-white/5">
          {t("fallbackScopeNote")}
        </div>
      )}

      <div className="mb-4 grid grid-cols-2 gap-3 sm:grid-cols-4">
        <StatCard
          icon={<IconCash className="size-4" />}
          label={t("cards.cost")}
          value={data ? usd(data.summary.cost_usd) : ""}
          hint={
            data
              ? t("cards.costHint", {
                  in: num(data.summary.input_tokens),
                  out: num(data.summary.output_tokens),
                })
              : ""
          }
          loading={isLoading}
        />
        <StatCard
          icon={<IconBolt className="size-4" />}
          label={t("cards.tokens")}
          value={data ? num(data.summary.input_tokens + data.summary.output_tokens) : ""}
          hint={
            data
              ? t("cards.tokensHint", {
                  in: num(data.summary.input_tokens),
                  out: num(data.summary.output_tokens),
                })
              : ""
          }
          loading={isLoading}
        />
        <StatCard
          icon={<IconMessage2 className="size-4" />}
          label={t("cards.turns")}
          value={data ? num(data.summary.turns) : ""}
          hint={
            data
              ? t("cards.turnsHint", { sessions: num(data.summary.sessions) })
              : ""
          }
          loading={isLoading}
        />
        <StatCard
          icon={<IconClock className="size-4" />}
          label={t("cards.latency")}
          value={data ? ms(data.summary.avg_latency_ms) : ""}
          hint={t("cards.latencyHint")}
          loading={isLoading}
        />
      </div>

      <div className="mb-4 grid gap-3 lg:grid-cols-2">
        <Card>
          <CardHeader>
            <CardTitle className="text-base">{t("charts.costTitle")}</CardTitle>
            <CardDescription>{t("charts.costDesc")}</CardDescription>
          </CardHeader>
          <CardContent>
            <SparkBar
              data={bars}
              height={120}
              emptyLabel={t("empty")}
              formatValue={usd}
            />
          </CardContent>
        </Card>
        <Card>
          <CardHeader>
            <CardTitle className="text-base">{t("charts.tokensTitle")}</CardTitle>
            <CardDescription>{t("charts.tokensDesc")}</CardDescription>
          </CardHeader>
          <CardContent>
            <SparkBar
              data={tokenBars}
              height={120}
              emptyLabel={t("empty")}
              color="rgb(34 197 94)"
              formatValue={num}
            />
          </CardContent>
        </Card>
      </div>

      <div className="grid gap-3 lg:grid-cols-2">
        <Card>
          <CardHeader>
            <CardTitle className="text-base">
              {t("topAgents.title")}
            </CardTitle>
            <CardDescription>{t("topAgents.desc")}</CardDescription>
          </CardHeader>
          <CardContent>
            {isLoading ? (
              <Skeleton className="h-24" />
            ) : !data?.top_agents.length ? (
              <p className="py-6 text-center text-xs sh-muted">{t("empty")}</p>
            ) : (
              <table className="w-full text-sm">
                <thead>
                  <tr className="border-b text-[11px] uppercase sh-muted">
                    <th className="py-1 text-left font-medium">
                      {t("topAgents.col.agent")}
                    </th>
                    <th className="py-1 text-right font-medium">
                      {t("topAgents.col.tokens")}
                    </th>
                    <th className="py-1 text-right font-medium">
                      {t("topAgents.col.cost")}
                    </th>
                    <th className="py-1 text-right font-medium">
                      {t("topAgents.col.turns")}
                    </th>
                  </tr>
                </thead>
                <tbody>
                  {data.top_agents.map((r) => (
                    <tr
                      key={r.agent_id ?? r.agent_name ?? "anon"}
                      className="border-b last:border-b-0"
                    >
                      <td className="py-1.5 truncate">
                        {r.agent_name ?? t("topAgents.deleted")}
                      </td>
                      <td className="py-1.5 text-right tabular-nums">
                        {num(r.input_tokens + r.output_tokens)}
                      </td>
                      <td className="py-1.5 text-right tabular-nums">
                        {usd(r.cost_usd)}
                      </td>
                      <td className="py-1.5 text-right tabular-nums">
                        {num(r.turns)}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </CardContent>
        </Card>
        <Card>
          <CardHeader>
            <CardTitle className="text-base">
              {t("topModels.title")}
            </CardTitle>
            <CardDescription>{t("topModels.desc")}</CardDescription>
          </CardHeader>
          <CardContent>
            {isLoading ? (
              <Skeleton className="h-24" />
            ) : !data?.top_models.length ? (
              <p className="py-6 text-center text-xs sh-muted">{t("empty")}</p>
            ) : (
              <table className="w-full text-sm">
                <thead>
                  <tr className="border-b text-[11px] uppercase sh-muted">
                    <th className="py-1 text-left font-medium">
                      {t("topModels.col.model")}
                    </th>
                    <th className="py-1 text-right font-medium">
                      {t("topModels.col.tokens")}
                    </th>
                    <th className="py-1 text-right font-medium">
                      {t("topModels.col.cost")}
                    </th>
                    <th className="py-1 text-right font-medium">
                      {t("topModels.col.turns")}
                    </th>
                  </tr>
                </thead>
                <tbody>
                  {data.top_models.map((r) => (
                    <tr
                      key={`${r.provider}/${r.model}`}
                      className="border-b last:border-b-0"
                    >
                      <td className="py-1.5 truncate">
                        <span className="font-mono text-[12px]">
                          {r.model}
                        </span>
                        <span className="ml-1 text-[10px] sh-muted">
                          ({r.provider})
                        </span>
                      </td>
                      <td className="py-1.5 text-right tabular-nums">
                        {num(r.input_tokens + r.output_tokens)}
                      </td>
                      <td className="py-1.5 text-right tabular-nums">
                        {usd(r.cost_usd)}
                      </td>
                      <td className="py-1.5 text-right tabular-nums">
                        {num(r.turns)}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </CardContent>
        </Card>
      </div>

      <SectionHeader title="" />
      <p className="mt-3 text-[11px] sh-muted">
        {t("footnote")}
      </p>
    </div>
  );
}

function StatCard({
  icon,
  label,
  value,
  hint,
  loading,
}: {
  icon: React.ReactNode;
  label: string;
  value: string;
  hint?: string;
  loading?: boolean;
}) {
  return (
    <Card>
      <CardContent className="py-3">
        <div className="mb-1 flex items-center gap-1.5 text-[11px] sh-muted">
          {icon}
          <span className="uppercase tracking-wide">{label}</span>
        </div>
        {loading ? (
          <Skeleton className="h-6 w-24" />
        ) : (
          <div className="text-xl font-semibold tabular-nums">{value}</div>
        )}
        {hint && <div className="mt-0.5 text-[11px] sh-muted">{hint}</div>}
      </CardContent>
    </Card>
  );
}

function toRange(k: WindowKey): { since: string; until: string } {
  const days = k === "7d" ? 7 : k === "30d" ? 30 : 90;
  const end = new Date();
  const start = new Date(end);
  start.setDate(start.getDate() - days);
  const fmt = (d: Date) => d.toISOString().slice(0, 10);
  return { since: fmt(start), until: fmt(end) };
}
