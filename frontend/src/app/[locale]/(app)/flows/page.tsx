"use client";

import { Link } from "@/lib/navigation";
import { IconPlus, IconRoute2 } from "@tabler/icons-react";
import { useLocale, useTranslations } from "next-intl";
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
import { useFlows } from "@/hooks/use-flows";
import { relativeTime } from "@/lib/utils";

export default function FlowsPage() {
  const t = useTranslations("flows");
  const locale = useLocale();
  const { data, isLoading } = useFlows();

  return (
    <div className="p-6">
      <PageHeader
        title={t("title")}
        description={t("description")}
        actions={
          <Button asChild size="sm">
            <Link href="/flows/new">
              <IconPlus className="size-4" />
              {t("new")}
            </Link>
          </Button>
        }
      />

      {isLoading && (
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3">
          {[0, 1, 2].map((i) => (
            <Skeleton key={i} className="h-28" />
          ))}
        </div>
      )}

      {!isLoading && (data ?? []).length === 0 && (
        <Card>
          <CardContent className="py-10 text-center text-sm sh-muted">
            {t("empty")}
          </CardContent>
        </Card>
      )}

      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3">
        {(data ?? []).map((f) => (
          <Card key={f.id}>
            <CardHeader>
              <div className="flex items-center gap-2">
                <IconRoute2 className="size-4 sh-muted" />
                <CardTitle className="flex-1 truncate text-base">
                  <Link
                    href={`/flows/${f.id}`}
                    className="hover:underline"
                  >
                    {f.name}
                  </Link>
                </CardTitle>
                {!f.enabled && (
                  <Badge variant="outline">{t("disabled")}</Badge>
                )}
              </div>
              {f.description && (
                <CardDescription className="line-clamp-2">
                  {f.description}
                </CardDescription>
              )}
            </CardHeader>
            <CardContent className="flex flex-wrap items-center gap-1.5 pt-0">
              <Badge variant="outline">{t(`trigger.${f.trigger_kind}`)}</Badge>
              {f.trigger_kind === "cron" && (
                <code className="rounded bg-black/5 px-1.5 py-0.5 text-[10px] dark:bg-white/5">
                  {String(
                    (f.trigger_config as Record<string, unknown>)?.expr ??
                      "—",
                  )}
                </code>
              )}
              <span className="ml-auto text-[10px] sh-muted">
                {f.last_run_at
                  ? t("lastRun", { when: relativeTime(f.last_run_at, locale) })
                  : t("neverRun")}
              </span>
            </CardContent>
          </Card>
        ))}
      </div>
    </div>
  );
}
