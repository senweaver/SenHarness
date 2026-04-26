"use client";

import { Link } from "@/lib/navigation";
import { useTranslations } from "next-intl";
import { IconPlus, IconProgressCheck } from "@tabler/icons-react";

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
import { useBatchRuns, type BatchRun } from "@/hooks/use-batch";

const STATUS_VARIANT: Record<BatchRun["status"], "default" | "primary" | "success" | "warning" | "destructive"> = {
  pending: "default",
  running: "warning",
  succeeded: "success",
  failed: "destructive",
  cancelled: "default",
};

export default function BatchListPage() {
  const t = useTranslations("batch");
  const { data, isLoading } = useBatchRuns();

  return (
    <div className="p-6">
      <PageHeader
        title={t("title")}
        description={t("description")}
        actions={
          <Link href="/batch/new">
            <Button size="sm">
              <IconPlus className="size-4" />
              {t("newBtn")}
            </Button>
          </Link>
        }
      />

      {isLoading && <Skeleton className="h-24" />}

      {!isLoading && (data ?? []).length === 0 && (
        <Card>
          <CardContent className="py-10 text-center text-sm sh-muted">
            {t("empty")}
          </CardContent>
        </Card>
      )}

      <div className="grid gap-3 sm:grid-cols-2">
        {(data ?? []).map((row) => {
          const stats = row.stats_json as {
            total?: number;
            passed?: number;
            failed?: number;
            skipped?: number;
            duration_ms?: number;
          };
          return (
            <Link key={row.id} href={`/batch/${row.id}`}>
              <Card className="transition-colors hover:bg-black/5 dark:hover:bg-white/5">
                <CardHeader>
                  <div className="flex items-center gap-2">
                    <CardTitle className="flex-1 truncate text-base">
                      {row.name}
                    </CardTitle>
                    <Badge variant={STATUS_VARIANT[row.status]}>
                      {row.status}
                    </Badge>
                  </div>
                  <CardDescription className="line-clamp-2">
                    {row.description || t("noDescription")}
                  </CardDescription>
                </CardHeader>
                <CardContent className="pt-0 text-[12px] sh-muted">
                  <div className="flex items-center gap-3">
                    <span>
                      {t("totalCases", { n: stats.total ?? 0 })}
                    </span>
                    {stats.passed !== undefined && (
                      <span className="text-emerald-600 dark:text-emerald-400">
                        {t("passed")} {stats.passed}
                      </span>
                    )}
                    {stats.failed !== undefined && stats.failed > 0 && (
                      <span className="text-rose-600 dark:text-rose-400">
                        {t("failed")} {stats.failed}
                      </span>
                    )}
                    {stats.duration_ms !== undefined && (
                      <span className="ml-auto flex items-center gap-1">
                        <IconProgressCheck className="size-3" />
                        {Math.round(stats.duration_ms / 100) / 10}s
                      </span>
                    )}
                  </div>
                </CardContent>
              </Card>
            </Link>
          );
        })}
      </div>
    </div>
  );
}
