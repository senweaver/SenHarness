"use client";

import { use, useMemo } from "react";
import { Link } from "@/lib/navigation";
import {
  IconArrowLeft,
  IconEdit,
  IconLoader2,
  IconPlayerPlay,
} from "@tabler/icons-react";
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
import { PageHeader } from "@/components/ui/page-header";
import { Skeleton } from "@/components/ui/skeleton";
import { FlowCanvas } from "@/components/flows/FlowCanvas";
import type {
  FlowGraphJson,
} from "@/components/flows/nodeTypes";
import {
  type FlowRunStatus,
  useFlow,
  useFlowRuns,
  useTriggerFlow,
} from "@/hooks/use-flows";
import { relativeTime } from "@/lib/utils";

export default function FlowDetailPage({
  params,
}: {
  params: Promise<{ flowId: string }>;
}) {
  const { flowId } = use(params);
  const locale = useLocale();
  const t = useTranslations("flows.detail");
  const tFlows = useTranslations("flows");

  const { data: flow, isLoading } = useFlow(flowId);
  const { data: runs } = useFlowRuns(flowId);
  const trigger = useTriggerFlow(flowId);

  const graph = (flow?.graph_json ?? {}) as unknown as FlowGraphJson;
  const hasGraph =
    Array.isArray(graph.nodes) && (graph.nodes?.length ?? 0) > 0;

  const latestRun = runs && runs[0];
  const nodeStatusMap = useMemo(() => {
    if (!hasGraph || !latestRun?.node_events_json) return {};
    const out: Record<
      string,
      "pending" | "running" | "success" | "failed"
    > = {};
    for (const ev of latestRun.node_events_json) {
      out[ev.node_id] = ev.status;
    }
    return out;
  }, [hasGraph, latestRun?.node_events_json]);

  const onRun = async () => {
    try {
      await trigger.mutateAsync({});
      toast.success(t("triggered"));
    } catch {
      toast.error(t("triggerFailed"));
    }
  };

  if (isLoading) {
    return (
      <div className="p-6">
        <Skeleton className="mb-4 h-8 w-64" />
        <Skeleton className="h-60" />
      </div>
    );
  }
  if (!flow) {
    return (
      <div className="p-6">
        <PageHeader title={t("notFound")} />
        <Button asChild variant="outline">
          <Link href="/flows">
            <IconArrowLeft className="size-4" />
            {t("backToList")}
          </Link>
        </Button>
      </div>
    );
  }

  return (
    <div className="p-6">
      <PageHeader
        title={flow.name}
        description={flow.description ?? undefined}
        actions={
          <div className="flex flex-wrap items-center gap-2">
            <Button asChild variant="outline" size="sm">
              <Link href="/flows">
                <IconArrowLeft className="size-4" />
              </Link>
            </Button>
            <Button asChild variant="outline" size="sm">
              <Link href={`/flows/${flow.id}/edit`}>
                <IconEdit className="size-4" />
                {tFlows("edit")}
              </Link>
            </Button>
            <Button size="sm" onClick={onRun} disabled={trigger.isPending}>
              {trigger.isPending ? (
                <IconLoader2 className="size-4 animate-spin" />
              ) : (
                <IconPlayerPlay className="size-4" />
              )}
              {t("runNow")}
            </Button>
          </div>
        }
      />

      <div className="mb-3 grid gap-3 lg:grid-cols-2">
        <Card>
          <CardHeader>
            <CardTitle className="text-base">{t("configTitle")}</CardTitle>
          </CardHeader>
          <CardContent className="space-y-1 text-sm">
            <Row
              label={t("trigger")}
              value={
                <Badge variant="outline">
                  {tFlows(`trigger.${flow.trigger_kind}`)}
                </Badge>
              }
            />
            <Row
              label={t("enabled")}
              value={
                <Badge variant={flow.enabled ? "primary" : "outline"}>
                  {flow.enabled ? t("yes") : t("no")}
                </Badge>
              }
            />
            <Row
              label={t("lastRun")}
              value={
                flow.last_run_at ? relativeTime(flow.last_run_at, locale) : "—"
              }
            />
            {flow.trigger_kind === "cron" && (
              <Row
                label="cron"
                value={
                  <code className="font-mono text-[11px]">
                    {String(
                      (flow.trigger_config as Record<string, unknown>)?.expr ??
                        "",
                    )}
                  </code>
                }
              />
            )}
          </CardContent>
        </Card>
        <Card>
          <CardHeader>
            <CardTitle className="text-base">{t("promptTitle")}</CardTitle>
            <CardDescription>
              {hasGraph ? t("graphModeHint") : t("promptDesc")}
            </CardDescription>
          </CardHeader>
          <CardContent>
            {hasGraph ? (
              <p className="text-xs sh-muted">
                {t("graphNodeCount", { n: graph.nodes.length })}
              </p>
            ) : (
              <pre className="whitespace-pre-wrap break-words rounded-md bg-black/5 p-3 font-mono text-[12px] dark:bg-white/5">
                {flow.prompt_template}
              </pre>
            )}
          </CardContent>
        </Card>
      </div>

      {hasGraph && (
        <Card className="mb-3 overflow-hidden p-0">
          <CardHeader className="px-4 pt-3">
            <CardTitle className="text-base">{t("liveTrace")}</CardTitle>
            <CardDescription>{t("liveTraceHint")}</CardDescription>
          </CardHeader>
          <div className="h-[360px] w-full">
            <FlowCanvas
              graph={graph}
              onGraphChange={() => {
                /* read-only on detail page */
              }}
              selectedNodeId={null}
              onSelectNode={() => {
                /* no-op */
              }}
              nodeStatus={nodeStatusMap}
              readOnly
            />
          </div>
        </Card>
      )}

      <Card>
        <CardHeader>
          <CardTitle className="text-base">{t("runsTitle")}</CardTitle>
          <CardDescription>{t("runsDesc")}</CardDescription>
        </CardHeader>
        <CardContent>
          {!runs?.length ? (
            <p className="py-6 text-center text-xs sh-muted">{t("runsEmpty")}</p>
          ) : (
            <ul className="flex flex-col gap-2">
              {runs.slice(0, 50).map((r) => (
                <li
                  key={r.id}
                  className="rounded-md border p-2 text-xs"
                >
                  <div className="flex items-center gap-2">
                    <StatusBadge status={r.status} />
                    <Badge variant="outline">
                      {tFlows(`trigger.${r.trigger_kind}`)}
                    </Badge>
                    <span className="ml-auto text-[10px] sh-muted">
                      {relativeTime(r.created_at, locale)}
                    </span>
                  </div>
                  {r.output_summary && (
                    <p className="mt-1 line-clamp-3 whitespace-pre-wrap text-[12px]">
                      {r.output_summary}
                    </p>
                  )}
                  {r.error && (
                    <p className="mt-1 text-[11px] text-red-600 dark:text-red-400">
                      {r.error}
                    </p>
                  )}
                  {r.session_id && (
                    <Link
                      href={`/chat/${r.session_id}`}
                      className="mt-1 inline-block text-[11px] text-[rgb(var(--color-primary))] hover:underline"
                    >
                      {t("openSession")}
                    </Link>
                  )}
                </li>
              ))}
            </ul>
          )}
        </CardContent>
      </Card>
    </div>
  );
}

function Row({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <div className="flex items-center justify-between gap-3">
      <span className="text-[11px] sh-muted">{label}</span>
      <span>{value}</span>
    </div>
  );
}

function StatusBadge({ status }: { status: FlowRunStatus }) {
  const map = {
    pending: "default",
    running: "warning",
    succeeded: "success",
    failed: "danger",
  } as const;
  return <Badge variant={map[status] ?? "default"}>{status}</Badge>;
}
