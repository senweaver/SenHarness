"use client";

import { useMemo, useState } from "react";
import { useSearchParams } from "next/navigation";
import { IconDownload, IconRefresh, IconSearch } from "@tabler/icons-react";
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
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { PageHeader } from "@/components/ui/page-header";
import { Skeleton } from "@/components/ui/skeleton";
import { useMe } from "@/hooks/use-me";
import {
  type AuditQuery,
  buildAuditCsvUrl,
  useAuditEvents,
} from "@/hooks/use-audit";
import { API_BASE_URL } from "@/lib/api";
import { useAuthStore } from "@/stores/auth-store";
import { useWorkspaceStore } from "@/stores/workspace-store";
import { relativeTime } from "@/lib/utils";

type WindowKey = "24h" | "7d" | "30d" | "90d";
type ScopeKey = "workspace" | "platform";

const ACTION_OPTIONS = [
  "",
  "auth.login",
  "auth.login_failed",
  "auth.logout",
  "agent.create",
  "agent.update",
  "agent.delete",
  "agent.visibility_change",
  "agent.report",
  "marketplace.clone",
  "squad.create",
  "squad.delete",
  "approval.decide",
  "report.decide",
];

/**
 * Plan §6.5 — twelve audit categories. Each chip narrows the list to
 * actions matching one of the prefixes; "all" clears the filter. The
 * backend stores the canonical action key, so this is a pure client
 * filter on top of the existing list query (no schema work needed).
 */
const AUDIT_CATEGORIES: Array<{
  key: string;
  prefixes: string[];
}> = [
  { key: "all", prefixes: [] },
  { key: "auth", prefixes: ["auth."] },
  { key: "member", prefixes: ["member.", "workspace.member.", "department."] },
  { key: "agent", prefixes: ["agent."] },
  { key: "channel", prefixes: ["channel."] },
  { key: "skill", prefixes: ["skill."] },
  { key: "approval", prefixes: ["approval."] },
  { key: "share", prefixes: ["marketplace.", "share."] },
  { key: "apikey", prefixes: ["api_key.", "runtime."] },
  { key: "permission", prefixes: ["permission.", "role.", "visibility."] },
  { key: "setting", prefixes: ["workspace.", "branding.", "provider."] },
  { key: "run_failure", prefixes: ["run.failed", "shield.", "budget.exceeded"] },
  { key: "platform_admin", prefixes: ["platform_admin."] },
];

export default function AuditPage() {
  const t = useTranslations("settings.audit");
  const tCommon = useTranslations("common");
  const locale = useLocale();
  const { data: me } = useMe();
  const isPlatformAdmin = me?.platform_role === "platform_admin";
  const searchParams = useSearchParams();

  const [winKey, setWinKey] = useState<WindowKey>("7d");
  const [scope, setScope] = useState<ScopeKey>(
    searchParams?.get("scope") === "platform" ? "platform" : "workspace",
  );
  const [action, setAction] = useState<string>("__ALL__");
  const [q, setQ] = useState("");
  const [category, setCategory] = useState<string>("all");

  const { since, until } = useMemo(() => toRange(winKey), [winKey]);

  const params: AuditQuery = useMemo(
    () => ({
      scope,
      since,
      until,
      action: action && action !== "__ALL__" ? action : undefined,
      q: q.trim() || undefined,
      limit: 200,
    }),
    [scope, since, until, action, q],
  );

  const { data, isLoading, isFetching, refetch } = useAuditEvents(params);

  const filtered = useMemo(() => {
    if (!data) return data;
    const cat = AUDIT_CATEGORIES.find((c) => c.key === category);
    if (!cat || cat.prefixes.length === 0) return data;
    return data.filter((row) =>
      cat.prefixes.some((prefix) => row.action.startsWith(prefix)),
    );
  }, [data, category]);

  const accessToken = useAuthStore((s) => s.accessToken);
  const ws = useWorkspaceStore((s) => s.activeWorkspaceId);

  const downloadCsv = async () => {
    const { url, headers } = buildAuditCsvUrl(
      API_BASE_URL,
      accessToken,
      ws,
      params,
    );
    try {
      const res = await fetch(url, { headers, credentials: "include" });
      if (!res.ok) throw new Error(`csv http ${res.status}`);
      const blob = await res.blob();
      const link = document.createElement("a");
      link.href = URL.createObjectURL(blob);
      link.download = `audit-${Date.now()}.csv`;
      document.body.appendChild(link);
      link.click();
      link.remove();
      toast.success(t("csvReady"));
    } catch {
      toast.error(t("csvFailed"));
    }
  };

  return (
    <div>
      <PageHeader
        title={t("title")}
        description={t("description")}
        actions={
          <div className="flex flex-wrap items-center gap-2">
            <Button
              variant="outline"
              size="sm"
              onClick={() => refetch()}
              disabled={isFetching}
              title={tCommon("refresh")}
            >
              <IconRefresh
                className={`size-4 ${isFetching ? "animate-spin" : ""}`}
              />
            </Button>
            <Button variant="outline" size="sm" onClick={downloadCsv}>
              <IconDownload className="size-4" />
              {t("exportCsv")}
            </Button>
          </div>
        }
      />

      <div className="mb-3 flex flex-wrap gap-1.5">
        {AUDIT_CATEGORIES.map((cat) => {
          const isActive = cat.key === category;
          return (
            <button
              key={cat.key}
              type="button"
              onClick={() => setCategory(cat.key)}
              className={
                isActive
                  ? "rounded-full bg-[rgb(var(--color-primary))] px-3 py-1 text-[11px] font-medium text-white shadow-sm"
                  : "rounded-full border bg-transparent px-3 py-1 text-[11px] font-medium sh-muted transition-colors hover:bg-black/5 dark:hover:bg-white/10"
              }
            >
              {(() => {
                try {
                  return t(`category.${cat.key}`);
                } catch {
                  return cat.key.replace(/_/g, " ");
                }
              })()}
            </button>
          );
        })}
      </div>

      <Card className="mb-3">
        <CardContent className="grid gap-3 py-3 sm:grid-cols-2 lg:grid-cols-4">
          <div>
            <label className="text-[11px] sh-muted">{t("filter.scope")}</label>
            <Select
              value={scope}
              onValueChange={(v) => setScope(v as ScopeKey)}
            >
              <SelectTrigger>
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="workspace">
                  {t("scope.workspace")}
                </SelectItem>
                {isPlatformAdmin && (
                  <SelectItem value="platform">
                    {t("scope.platform")}
                  </SelectItem>
                )}
              </SelectContent>
            </Select>
          </div>
          <div>
            <label className="text-[11px] sh-muted">{t("filter.window")}</label>
            <Select
              value={winKey}
              onValueChange={(v) => setWinKey(v as WindowKey)}
            >
              <SelectTrigger>
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="24h">{t("window.24h")}</SelectItem>
                <SelectItem value="7d">{t("window.7d")}</SelectItem>
                <SelectItem value="30d">{t("window.30d")}</SelectItem>
                <SelectItem value="90d">{t("window.90d")}</SelectItem>
              </SelectContent>
            </Select>
          </div>
          <div>
            <label className="text-[11px] sh-muted">{t("filter.action")}</label>
            <Select value={action} onValueChange={setAction}>
              <SelectTrigger>
                <SelectValue placeholder={t("actionAny")} />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="__ALL__">{t("actionAny")}</SelectItem>
                {ACTION_OPTIONS.filter((a) => a).map((a) => (
                  <SelectItem key={a} value={a}>
                    {a}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
          <div>
            <label className="text-[11px] sh-muted">{t("filter.search")}</label>
            <div className="relative">
              <IconSearch className="pointer-events-none absolute left-2 top-1/2 size-3.5 -translate-y-1/2 sh-muted" />
              <Input
                value={q}
                onChange={(e) => setQ(e.target.value)}
                placeholder={t("searchPlaceholder")}
                className="pl-7"
              />
            </div>
          </div>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle className="text-base">
            {t("tableTitle", { count: filtered?.length ?? 0 })}
          </CardTitle>
          <CardDescription>{t("tableDesc")}</CardDescription>
        </CardHeader>
        <CardContent>
          {isLoading ? (
            <Skeleton className="h-60" />
          ) : !filtered?.length ? (
            <p className="py-6 text-center text-xs sh-muted">{t("empty")}</p>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead>
                  <tr className="border-b text-[11px] uppercase sh-muted">
                    <th className="w-[128px] py-2 text-left font-medium">
                      {t("col.time")}
                    </th>
                    <th className="w-[200px] py-2 text-left font-medium">
                      {t("col.actor")}
                    </th>
                    <th className="w-[180px] py-2 text-left font-medium">
                      {t("col.action")}
                    </th>
                    <th className="py-2 text-left font-medium">
                      {t("col.summary")}
                    </th>
                    <th className="w-[120px] py-2 text-left font-medium">
                      {t("col.ip")}
                    </th>
                  </tr>
                </thead>
                <tbody>
                  {filtered.map((ev) => (
                    <tr
                      key={ev.id}
                      className="border-b last:border-b-0 align-top"
                    >
                      <td
                        className="py-1.5 font-mono text-[11px] sh-muted"
                        title={new Date(ev.created_at).toLocaleString(locale)}
                      >
                        {relativeTime(ev.created_at, locale)}
                      </td>
                      <td className="py-1.5 text-[13px]">
                        {ev.actor_name ? (
                          <>
                            <div className="truncate">{ev.actor_name}</div>
                            <div className="truncate text-[10px] sh-muted">
                              {ev.actor_email}
                            </div>
                          </>
                        ) : (
                          <span className="sh-muted">(system)</span>
                        )}
                      </td>
                      <td className="py-1.5">
                        <Badge
                          variant={actionVariant(ev.action)}
                          className="font-mono text-[10px]"
                        >
                          {ev.action}
                        </Badge>
                      </td>
                      <td className="py-1.5">
                        <div className="break-words">{ev.summary ?? "—"}</div>
                        {Object.keys(ev.metadata_json ?? {}).length > 0 && (
                          <details className="mt-0.5">
                            <summary className="cursor-pointer text-[11px] sh-muted">
                              {t("metadata")}
                            </summary>
                            <pre className="mt-1 overflow-x-auto rounded bg-black/5 p-1.5 text-[10px] dark:bg-white/5">
                              {JSON.stringify(ev.metadata_json, null, 2)}
                            </pre>
                          </details>
                        )}
                      </td>
                      <td className="py-1.5 font-mono text-[10px] sh-muted">
                        {ev.ip_address ?? "—"}
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

function actionVariant(
  a: string,
): "default" | "outline" | "danger" | "primary" {
  if (a.endsWith(".delete")) return "danger";
  if (a === "auth.login_failed") return "danger";
  if (a === "report.decide" || a === "approval.decide") return "primary";
  if (a.startsWith("auth.")) return "outline";
  return "default";
}

function toRange(k: WindowKey): { since: string; until: string } {
  const end = new Date();
  const start = new Date(end);
  const days =
    k === "24h" ? 1 : k === "7d" ? 7 : k === "30d" ? 30 : 90;
  start.setDate(start.getDate() - days);
  const fmt = (d: Date) => d.toISOString().slice(0, 10);
  // Use +1 day for until since the backend treats it as exclusive upper bound.
  const endPlus = new Date(end);
  endPlus.setDate(endPlus.getDate() + 1);
  return { since: fmt(start), until: fmt(endPlus) };
}
