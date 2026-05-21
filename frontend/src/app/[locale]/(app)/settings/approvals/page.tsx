"use client";

import { useMemo, useState } from "react";
import { Link } from "@/lib/navigation";
import { IconArrowRight, IconArchive, IconSearch } from "@tabler/icons-react";
import { useLocale, useTranslations } from "next-intl";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
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
import { useRecentApprovals } from "@/hooks/use-approvals";
import { relativeTime } from "@/lib/utils";
import type { ApprovalRead } from "@/types/api";

type StatusKey = "all" | "approved" | "denied" | "expired";

/**
 * Settings → Approvals (archive). Live/pending items live on the top-level
 * `/approvals` page. This view is scoped to historical decisions and is
 * intentionally read-only.
 */
export default function ApprovalsArchivePage() {
  const t = useTranslations("settings.approvals");
  const tStatus = useTranslations("approvals.status");
  const locale = useLocale();
  const recent = useRecentApprovals();

  const [status, setStatus] = useState<StatusKey>("all");
  const [q, setQ] = useState("");

  const rows = useMemo(() => {
    const all = (recent.data ?? []).filter((r) => r.status !== "pending");
    const byStatus = status === "all" ? all : all.filter((r) => r.status === status);
    const needle = q.trim().toLowerCase();
    if (!needle) return byStatus;
    return byStatus.filter(
      (r) =>
        r.tool_name.toLowerCase().includes(needle) ||
        (r.summary ?? "").toLowerCase().includes(needle) ||
        (r.decided_reason ?? "").toLowerCase().includes(needle),
    );
  }, [recent.data, status, q]);

  return (
    <div>
      <PageHeader
        title={t("title")}
        description={t("description")}
        actions={
          <Button asChild size="sm">
            <Link href="/approvals">
              {t("goToLiveQueue")}
              <IconArrowRight className="size-4" />
            </Link>
          </Button>
        }
      />

      <Card className="mb-3">
        <CardContent className="grid gap-3 py-3 sm:grid-cols-3">
          <div>
            <label className="text-[11px] sh-muted">{t("filter.status")}</label>
            <Select value={status} onValueChange={(v) => setStatus(v as StatusKey)}>
              <SelectTrigger>
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="all">{t("filter.allStatuses")}</SelectItem>
                <SelectItem value="approved">{tStatus("approved")}</SelectItem>
                <SelectItem value="denied">{tStatus("denied")}</SelectItem>
                <SelectItem value="expired">{tStatus("expired")}</SelectItem>
              </SelectContent>
            </Select>
          </div>
          <div className="sm:col-span-2">
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
          <CardTitle className="flex items-center gap-2 text-sm">
            <IconArchive className="size-4 sh-muted" />
            {t("archiveCount", { n: rows.length })}
          </CardTitle>
        </CardHeader>
        <CardContent>
          {recent.isLoading ? (
            <Skeleton className="h-60" />
          ) : rows.length === 0 ? (
            <p className="py-8 text-center text-xs sh-muted">{t("empty")}</p>
          ) : (
            <div className="divide-y">
              {rows.slice(0, 200).map((row) => (
                <HistoryRow key={row.id} row={row} locale={locale} />
              ))}
            </div>
          )}
        </CardContent>
      </Card>
    </div>
  );
}

function HistoryRow({ row, locale }: { row: ApprovalRead; locale: string }) {
  const t = useTranslations("approvals");
  const statusBadge = (() => {
    switch (row.status) {
      case "approved":
        return (
          <Badge variant="default" className="bg-emerald-600 text-white">
            {t("status.approved")}
          </Badge>
        );
      case "denied":
        return <Badge variant="danger">{t("status.denied")}</Badge>;
      case "expired":
        return <Badge variant="outline">{t("status.expired")}</Badge>;
      default:
        return <Badge variant="outline">{row.status}</Badge>;
    }
  })();

  return (
    <div className="grid grid-cols-[140px_100px_1fr_140px] items-start gap-2 py-2 text-xs">
      <span
        className="font-mono sh-muted"
        title={new Date(row.decided_at ?? row.created_at).toLocaleString(locale)}
      >
        {relativeTime(row.decided_at ?? row.created_at, locale)}
      </span>
      <span className="font-mono">{row.tool_name}</span>
      <div className="min-w-0">
        <div className="break-all">{row.summary ?? "—"}</div>
        {row.decided_reason && (
          <div className="mt-0.5 break-words text-[11px] sh-muted">
            {t("reasonLabel")}: {row.decided_reason}
          </div>
        )}
      </div>
      <div className="flex justify-end">{statusBadge}</div>
    </div>
  );
}
