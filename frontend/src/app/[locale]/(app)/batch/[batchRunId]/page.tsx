"use client";

import { use, useState } from "react";
import { Link } from "@/lib/navigation";
import { useTranslations } from "next-intl";
import { IconArrowLeft, IconClock, IconGitCompare } from "@tabler/icons-react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { PageHeader } from "@/components/ui/page-header";
import { useBatchRun, type BatchRunCase } from "@/hooks/use-batch";

const CASE_STATUS_VARIANT: Record<
  BatchRunCase["status"],
  "default" | "primary" | "success" | "warning" | "destructive"
> = {
  pending: "default",
  running: "warning",
  succeeded: "success",
  failed: "destructive",
  skipped: "default",
};

export default function BatchDetailPage({
  params,
}: {
  params: Promise<{ batchRunId: string }>;
}) {
  const { batchRunId } = use(params);
  const t = useTranslations("batch.detail");
  const { data, isLoading } = useBatchRun(batchRunId);
  const [expanded, setExpanded] = useState<string | null>(null);

  if (isLoading || !data) {
    return (
      <div className="p-6">
        <Skeleton className="h-40" />
      </div>
    );
  }

  const stats = data.stats_json as {
    total?: number;
    passed?: number;
    failed?: number;
    skipped?: number;
    duration_ms?: number;
  };

  return (
    <div className="p-6">
      <PageHeader
        title={data.name}
        description={data.description ?? t("noDescription")}
        actions={
          <Link href="/batch">
            <Button variant="ghost" size="sm">
              <IconArrowLeft className="size-4" />
              {t("back")}
            </Button>
          </Link>
        }
      />

      <div className="mb-4 grid gap-3 sm:grid-cols-4">
        <Stat label={t("status")}>
          <Badge
            variant={
              data.status === "succeeded"
                ? "success"
                : data.status === "failed"
                  ? "destructive"
                  : data.status === "running"
                    ? "warning"
                    : "default"
            }
          >
            {data.status}
          </Badge>
        </Stat>
        <Stat label={t("total")}>{stats.total ?? data.cases.length}</Stat>
        <Stat label={t("passed")}>
          <span className="text-emerald-600 dark:text-emerald-400">
            {stats.passed ?? 0}
          </span>
        </Stat>
        <Stat label={t("failed")}>
          <span className="text-rose-600 dark:text-rose-400">
            {stats.failed ?? 0}
          </span>
        </Stat>
      </div>

      <div className="space-y-3">
        {data.cases.map((c) => (
          <Card key={c.id}>
            <CardHeader>
              <div className="flex items-center gap-2">
                <CardTitle className="flex-1 truncate text-sm">
                  {c.case_label ?? c.id.slice(0, 8)}
                </CardTitle>
                <Badge variant={CASE_STATUS_VARIANT[c.status]}>{c.status}</Badge>
                {c.duration_ms !== null && (
                  <span className="flex items-center gap-1 text-[11px] sh-muted">
                    <IconClock className="size-3" />
                    {Math.round((c.duration_ms ?? 0) / 100) / 10}s
                  </span>
                )}
              </div>
              <CardDescription className="line-clamp-1 font-mono text-[11px]">
                {c.input_text.slice(0, 200)}
              </CardDescription>
            </CardHeader>
            <CardContent className="space-y-2 pt-0 text-[12px]">
              {c.baseline_text !== null && (
                <Row label={t("baseline")}>
                  <pre className="whitespace-pre-wrap break-words font-mono text-[11px] sh-muted">
                    {(c.baseline_text ?? "").slice(0, 500)}
                  </pre>
                </Row>
              )}
              {c.output_text !== null && (
                <Row label={t("candidate")}>
                  <pre className="whitespace-pre-wrap break-words font-mono text-[11px]">
                    {(c.output_text ?? "").slice(0, 500)}
                  </pre>
                </Row>
              )}
              {c.diff_json?.similarity !== undefined && (
                <Row label={t("similarity")}>
                  <span>
                    {Math.round((c.diff_json.similarity ?? 0) * 100)}%
                  </span>
                </Row>
              )}
              {c.error && (
                <Row label={t("error")}>
                  <code className="text-rose-600 dark:text-rose-400">
                    {c.error}
                  </code>
                </Row>
              )}
              {c.diff_json?.unified_diff && (
                <details
                  open={expanded === c.id}
                  onToggle={(e) => {
                    const isOpen = (e.target as HTMLDetailsElement).open;
                    setExpanded(isOpen ? c.id : null);
                  }}
                >
                  <summary className="cursor-pointer text-[11px] sh-muted">
                    <IconGitCompare className="mr-1 inline size-3" />
                    {t("unifiedDiff")}
                  </summary>
                  <pre className="mt-2 overflow-x-auto rounded bg-black/5 p-2 font-mono text-[11px] dark:bg-white/5">
                    {c.diff_json.unified_diff}
                  </pre>
                </details>
              )}
            </CardContent>
          </Card>
        ))}
      </div>
    </div>
  );
}

function Stat({
  label,
  children,
}: {
  label: string;
  children: React.ReactNode;
}) {
  return (
    <Card>
      <CardContent className="py-4">
        <div className="text-[11px] sh-muted">{label}</div>
        <div className="mt-1 text-lg font-medium">{children}</div>
      </CardContent>
    </Card>
  );
}

function Row({
  label,
  children,
}: {
  label: string;
  children: React.ReactNode;
}) {
  return (
    <div>
      <div className="text-[11px] sh-muted">{label}</div>
      {children}
    </div>
  );
}
