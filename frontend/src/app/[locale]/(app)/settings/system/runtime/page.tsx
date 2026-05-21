"use client";

import { useMemo, useState } from "react";
import { Link } from "@/lib/navigation";
import {
  IconCopy,
  IconRefresh,
  IconBolt,
  IconShieldCheck,
} from "@tabler/icons-react";
import { useLocale, useTranslations } from "next-intl";
import { toast } from "sonner";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { PageHeader } from "@/components/ui/page-header";
import { Skeleton } from "@/components/ui/skeleton";
import { useMe } from "@/hooks/use-me";
import {
  useForceRecycle,
  useInflightRuns,
  useRuntimeStats,
} from "@/hooks/use-runtime-console";
import { cn, relativeTime } from "@/lib/utils";
import type {
  InflightRunRow,
  InflightRunStateBucket,
} from "@/types/api";

const LAST_SEEN_WARN_SECONDS = 5 * 60;

const BUCKET_TO_BADGE: Record<
  InflightRunStateBucket,
  "success" | "warning" | "default" | "danger"
> = {
  running: "success",
  paused: "warning",
  lost: "default",
  zombie: "danger",
  killed: "default",
};

export default function RuntimeConsolePage() {
  const t = useTranslations("runtimeConsole");
  const tCommon = useTranslations("common");
  const locale = useLocale();
  const { data: me } = useMe();
  const isPlatformAdmin = me?.platform_role === "platform_admin";
  const isWorkspaceAdmin =
    me?.current_role === "owner" || me?.current_role === "admin";
  const allowed = isPlatformAdmin || isWorkspaceAdmin;

  const stats = useRuntimeStats();
  const inflight = useInflightRuns({ limit: 200 });
  const recycle = useForceRecycle();

  const [confirmRow, setConfirmRow] = useState<InflightRunRow | null>(null);

  const rows = useMemo(() => inflight.data?.rows ?? [], [inflight.data]);

  if (!allowed) {
    return (
      <div className="space-y-4">
        <PageHeader title={t("pageTitle")} description={t("pageDescription")} />
        <Card>
          <CardContent className="py-10 text-center sh-muted">
            <IconShieldCheck className="mx-auto mb-2 size-6" />
            <p className="text-sm">{t("forbidden")}</p>
          </CardContent>
        </Card>
      </div>
    );
  }

  const onCopy = async (value: string) => {
    try {
      await navigator.clipboard.writeText(value);
      toast.success(t("copied"));
    } catch {
      toast.error(t("copyFailed"));
    }
  };

  const onConfirmRecycle = async () => {
    if (!confirmRow) return;
    try {
      await recycle.mutateAsync({ runId: confirmRow.run_id });
      toast.success(t("forceRecycleSuccessToast"));
      setConfirmRow(null);
    } catch (err) {
      const message =
        err instanceof Error ? err.message : t("forceRecycleFailedToast");
      toast.error(message || t("forceRecycleFailedToast"));
    }
  };

  return (
    <div className="space-y-5">
      <PageHeader
        title={t("pageTitle")}
        description={t("pageDescription")}
        actions={
          <Button
            variant="outline"
            size="sm"
            onClick={() => {
              stats.refetch();
              inflight.refetch();
            }}
            disabled={inflight.isFetching || stats.isFetching}
            title={tCommon("refresh")}
          >
            <IconRefresh
              className={cn(
                "size-4",
                (inflight.isFetching || stats.isFetching) && "animate-spin",
              )}
            />
          </Button>
        }
      />

      <StatsStrip
        loading={stats.isLoading}
        running={stats.data?.running ?? 0}
        paused={stats.data?.paused ?? 0}
        lost={stats.data?.lost ?? 0}
        zombie={stats.data?.zombie ?? 0}
        totalActive={stats.data?.total_active ?? 0}
        labels={{
          running: t("statRunning"),
          paused: t("statPaused"),
          lost: t("statLost"),
          zombie: t("statZombie"),
          totalActive: t("statTotalActive"),
        }}
      />

      <Card>
        <CardHeader>
          <CardTitle className="text-base">
            {t("tableTitle", { count: rows.length })}
          </CardTitle>
        </CardHeader>
        <CardContent>
          {inflight.isLoading ? (
            <Skeleton className="h-48" />
          ) : rows.length === 0 ? (
            <p className="py-8 text-center text-xs sh-muted">{t("empty")}</p>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead>
                  <tr className="border-b text-[11px] uppercase sh-muted">
                    <th className="py-2 pr-3 text-left font-medium">
                      {t("runIdLabel")}
                    </th>
                    <th className="py-2 pr-3 text-left font-medium">
                      {t("sessionLabel")}
                    </th>
                    <th className="py-2 pr-3 text-left font-medium">
                      {t("agentLabel")}
                    </th>
                    <th className="py-2 pr-3 text-left font-medium">
                      {t("ownerLabel")}
                    </th>
                    <th className="py-2 pr-3 text-left font-medium">
                      {t("backendKindLabel")}
                    </th>
                    <th className="py-2 pr-3 text-left font-medium">
                      {t("stateLabel")}
                    </th>
                    <th className="py-2 pr-3 text-left font-medium">
                      {t("startedLabel")}
                    </th>
                    <th className="py-2 pr-3 text-left font-medium">
                      {t("lastSeenLabel")}
                    </th>
                    <th className="py-2 pr-3 text-right font-medium">
                      {t("elapsedLabel")}
                    </th>
                    <th className="py-2 pr-3 text-right font-medium">
                      {t("tokenEstLabel")}
                    </th>
                    <th className="py-2 text-right font-medium">
                      {t("actionsLabel")}
                    </th>
                  </tr>
                </thead>
                <tbody>
                  {rows.map((row) => (
                    <Row
                      key={row.inflight_run_id}
                      row={row}
                      locale={locale}
                      onCopy={onCopy}
                      onForceRecycle={() => setConfirmRow(row)}
                      labels={{
                        copyTooltip: t("copyRunId"),
                        forceRecycleButton: t("forceRecycleButton"),
                        stateRunning: t("stateRunning"),
                        statePaused: t("statePaused"),
                        stateLost: t("stateLost"),
                        stateZombie: t("stateZombie"),
                        stateKilled: t("stateKilled"),
                      }}
                    />
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </CardContent>
      </Card>

      <Dialog
        open={Boolean(confirmRow)}
        onOpenChange={(open) => {
          if (!open) setConfirmRow(null);
        }}
      >
        <DialogContent>
          <DialogHeader>
            <DialogTitle>{t("forceRecycleDialogTitle")}</DialogTitle>
            <DialogDescription>
              {t("forceRecycleDialogBody")}
            </DialogDescription>
          </DialogHeader>
          {confirmRow && (
            <div className="space-y-1 rounded border p-3 text-[12px]">
              <div>
                <span className="sh-muted">{t("runIdLabel")}: </span>
                <span className="font-mono">
                  {shortRunId(confirmRow.run_id)}
                </span>
              </div>
              <div>
                <span className="sh-muted">{t("agentLabel")}: </span>
                <span>{confirmRow.agent_name ?? "—"}</span>
              </div>
              <div>
                <span className="sh-muted">{t("ownerLabel")}: </span>
                <span>{confirmRow.identity_email ?? "—"}</span>
              </div>
            </div>
          )}
          <DialogFooter>
            <Button
              variant="outline"
              onClick={() => setConfirmRow(null)}
              disabled={recycle.isPending}
            >
              {tCommon("cancel")}
            </Button>
            <Button
              variant="destructive"
              onClick={onConfirmRecycle}
              disabled={recycle.isPending}
            >
              <IconBolt className="size-4" />
              {t("forceRecycleConfirm")}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}

function StatsStrip({
  loading,
  running,
  paused,
  lost,
  zombie,
  totalActive,
  labels,
}: {
  loading: boolean;
  running: number;
  paused: number;
  lost: number;
  zombie: number;
  totalActive: number;
  labels: {
    running: string;
    paused: string;
    lost: string;
    zombie: string;
    totalActive: string;
  };
}) {
  const cards: Array<{
    label: string;
    value: number;
    accent: string;
  }> = [
    { label: labels.running, value: running, accent: "text-green-600 dark:text-green-400" },
    { label: labels.paused, value: paused, accent: "text-amber-600 dark:text-amber-400" },
    { label: labels.lost, value: lost, accent: "sh-muted" },
    { label: labels.zombie, value: zombie, accent: "text-red-600 dark:text-red-400" },
    { label: labels.totalActive, value: totalActive, accent: "" },
  ];
  return (
    <div className="grid grid-cols-2 gap-3 md:grid-cols-5">
      {cards.map((card) => (
        <Card key={card.label}>
          <CardContent className="py-4">
            <div className="text-[11px] uppercase tracking-wide sh-muted">
              {card.label}
            </div>
            <div className={cn("mt-1 text-2xl font-semibold", card.accent)}>
              {loading ? "—" : card.value}
            </div>
          </CardContent>
        </Card>
      ))}
    </div>
  );
}

function Row({
  row,
  locale,
  onCopy,
  onForceRecycle,
  labels,
}: {
  row: InflightRunRow;
  locale: string;
  onCopy: (value: string) => void;
  onForceRecycle: () => void;
  labels: {
    copyTooltip: string;
    forceRecycleButton: string;
    stateRunning: string;
    statePaused: string;
    stateLost: string;
    stateZombie: string;
    stateKilled: string;
  };
}) {
  const lastSeenAge = ageSeconds(row.last_seen_at);
  const lastSeenIsStale = lastSeenAge >= LAST_SEEN_WARN_SECONDS;
  const stateLabel =
    row.state_bucket === "running"
      ? labels.stateRunning
      : row.state_bucket === "paused"
        ? labels.statePaused
        : row.state_bucket === "lost"
          ? labels.stateLost
          : row.state_bucket === "zombie"
            ? labels.stateZombie
            : labels.stateKilled;
  const canRecycle =
    row.state_bucket === "running" || row.state_bucket === "paused";

  return (
    <tr className="border-b last:border-b-0 align-top">
      <td className="py-2 pr-3">
        <div className="flex items-center gap-1">
          <span className="font-mono text-[11px]">{shortRunId(row.run_id)}</span>
          <button
            type="button"
            className="rounded p-0.5 sh-muted hover:bg-black/5 dark:hover:bg-white/10"
            onClick={() => onCopy(row.run_id)}
            title={labels.copyTooltip}
            aria-label={labels.copyTooltip}
          >
            <IconCopy className="size-3" />
          </button>
        </div>
      </td>
      <td className="py-2 pr-3">
        <Link
          href={`/chat/${row.session_id}`}
          className="text-[13px] underline-offset-2 hover:underline"
        >
          {row.session_label || shortRunId(row.session_id)}
        </Link>
      </td>
      <td className="py-2 pr-3 text-[13px]">
        {row.agent_id ? (
          <Link
            href={`/agents/${row.agent_id}`}
            className="underline-offset-2 hover:underline"
          >
            {row.agent_name || "—"}
          </Link>
        ) : (
          <span className="sh-muted">—</span>
        )}
      </td>
      <td className="py-2 pr-3 text-[13px]">
        {row.identity_email ?? <span className="sh-muted">—</span>}
      </td>
      <td className="py-2 pr-3">
        <Badge variant="outline" className="font-mono text-[10px]">
          {row.backend_kind}
        </Badge>
      </td>
      <td className="py-2 pr-3">
        <Badge variant={BUCKET_TO_BADGE[row.state_bucket] ?? "default"}>
          {stateLabel}
        </Badge>
      </td>
      <td
        className="py-2 pr-3 font-mono text-[11px] sh-muted"
        title={new Date(row.started_at).toLocaleString(locale)}
      >
        {relativeTime(row.started_at, locale)}
      </td>
      <td
        className={cn(
          "py-2 pr-3 font-mono text-[11px]",
          lastSeenIsStale ? "text-red-600 dark:text-red-400" : "sh-muted",
        )}
        title={new Date(row.last_seen_at).toLocaleString(locale)}
      >
        {relativeTime(row.last_seen_at, locale)}
      </td>
      <td className="py-2 pr-3 text-right font-mono text-[12px]">
        {formatElapsed(row.elapsed_seconds)}
      </td>
      <td className="py-2 pr-3 text-right font-mono text-[12px]">
        {row.token_estimate == null ? (
          <span className="sh-muted">—</span>
        ) : (
          row.token_estimate.toLocaleString(locale)
        )}
      </td>
      <td className="py-2 text-right">
        <Button
          variant="destructive"
          size="sm"
          disabled={!canRecycle}
          onClick={onForceRecycle}
        >
          {labels.forceRecycleButton}
        </Button>
      </td>
    </tr>
  );
}

function shortRunId(id: string): string {
  return id.slice(0, 8);
}

function formatElapsed(seconds: number): string {
  const total = Math.max(0, Math.round(seconds));
  const hh = Math.floor(total / 3600);
  const mm = Math.floor((total % 3600) / 60);
  const ss = total % 60;
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${pad(hh)}:${pad(mm)}:${pad(ss)}`;
}

function ageSeconds(iso: string): number {
  const then = new Date(iso).getTime();
  return Math.max(0, (Date.now() - then) / 1000);
}
