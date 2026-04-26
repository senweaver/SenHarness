"use client";

import { useState } from "react";
import { Link } from "@/lib/navigation";
import {
  IconCheck,
  IconExternalLink,
  IconFlag,
  IconLoader2,
  IconShieldOff,
  IconTrash,
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
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { PageHeader } from "@/components/ui/page-header";
import { Skeleton } from "@/components/ui/skeleton";
import {
  type AgentReport,
  type ReportStatus,
  useDecideReport,
  useReports,
} from "@/hooks/use-moderation";
import { relativeTime } from "@/lib/utils";

export default function ModerationPage() {
  const t = useTranslations("settings.moderation");
  const [statusFilter, setStatusFilter] = useState<"all" | ReportStatus>(
    "pending",
  );

  const filter = statusFilter === "all" ? undefined : statusFilter;
  const { data, isLoading } = useReports(filter);

  const rows = data ?? [];

  return (
    <div>
      <PageHeader
        title={t("title")}
        description={t("description")}
        actions={
          <Select
            value={statusFilter}
            onValueChange={(v) =>
              setStatusFilter(v as "all" | ReportStatus)
            }
          >
            <SelectTrigger className="w-[160px]">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="pending">{t("filter.pending")}</SelectItem>
              <SelectItem value="reviewed">{t("filter.reviewed")}</SelectItem>
              <SelectItem value="dismissed">{t("filter.dismissed")}</SelectItem>
              <SelectItem value="removed">{t("filter.removed")}</SelectItem>
              <SelectItem value="all">{t("filter.all")}</SelectItem>
            </SelectContent>
          </Select>
        }
      />

      {isLoading && <Skeleton className="h-60" />}

      {!isLoading && rows.length === 0 && (
        <Card>
          <CardContent className="py-10 text-center text-sm sh-muted">
            {t("empty")}
          </CardContent>
        </Card>
      )}

      <div className="flex flex-col gap-2">
        {rows.map((row) => (
          <ReportRow key={row.id} row={row} />
        ))}
      </div>
    </div>
  );
}

function ReportRow({ row }: { row: AgentReport }) {
  const t = useTranslations("settings.moderation");
  const locale = useLocale();
  const decide = useDecideReport();
  const [submitting, setSubmitting] = useState<ReportStatus | null>(null);

  const onDecide = async (decision: ReportStatus) => {
    setSubmitting(decision);
    try {
      await decide.mutateAsync({ reportId: row.id, decision });
      toast.success(t(`decided.${decision}`));
    } catch {
      toast.error(t("decideFailed"));
    } finally {
      setSubmitting(null);
    }
  };

  const statusBadge: Record<ReportStatus, React.ReactNode> = {
    pending: <Badge variant="warning">{t("status.pending")}</Badge>,
    reviewed: <Badge variant="outline">{t("status.reviewed")}</Badge>,
    dismissed: <Badge variant="outline">{t("status.dismissed")}</Badge>,
    removed: <Badge variant="danger">{t("status.removed")}</Badge>,
  };

  return (
    <Card>
      <CardHeader className="pb-2">
        <div className="flex items-center gap-2">
          <IconFlag className="size-4 text-amber-500" />
          <CardTitle className="flex-1 truncate text-base">
            {row.agent_name ?? row.agent_id}
          </CardTitle>
          {statusBadge[row.status]}
        </div>
        <CardDescription className="flex items-center gap-2 text-[11px]">
          <span>
            {t("reportedBy", {
              name: row.reporter_name ?? "(anonymous)",
            })}
          </span>
          <span>·</span>
          <span>{relativeTime(row.created_at, locale)}</span>
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-2">
        <div className="flex flex-wrap gap-1.5">
          <Badge variant="default" className="font-mono">
            {t(`reason.${row.reason}`)}
          </Badge>
        </div>
        {row.detail && (
          <p className="rounded-md border border-dashed bg-black/2 p-2 text-xs dark:bg-white/5">
            {row.detail}
          </p>
        )}
        {row.review_decision && (
          <p className="text-[11px] sh-muted">
            {t("reviewerNote", {
              name: row.reviewer_name ?? "moderator",
              note: row.review_decision,
            })}
          </p>
        )}
        <div className="flex flex-wrap items-center gap-2">
          <Button asChild size="sm" variant="outline">
            <Link href={`/agents/${row.agent_id}`} target="_blank">
              <IconExternalLink className="size-3.5" />
              {t("openAgent")}
            </Link>
          </Button>
          {row.status === "pending" && (
            <>
              <Button
                size="sm"
                variant="outline"
                className="border-green-500 text-green-600"
                disabled={submitting !== null}
                onClick={() => onDecide("dismissed")}
              >
                {submitting === "dismissed" ? (
                  <IconLoader2 className="size-3.5 animate-spin" />
                ) : (
                  <IconCheck className="size-3.5" />
                )}
                {t("actions.dismiss")}
              </Button>
              <Button
                size="sm"
                variant="outline"
                disabled={submitting !== null}
                onClick={() => onDecide("reviewed")}
              >
                {submitting === "reviewed" ? (
                  <IconLoader2 className="size-3.5 animate-spin" />
                ) : (
                  <IconShieldOff className="size-3.5" />
                )}
                {t("actions.reviewed")}
              </Button>
              <Button
                size="sm"
                variant="destructive"
                disabled={submitting !== null}
                onClick={() => onDecide("removed")}
              >
                {submitting === "removed" ? (
                  <IconLoader2 className="size-3.5 animate-spin" />
                ) : (
                  <IconTrash className="size-3.5" />
                )}
                {t("actions.remove")}
              </Button>
            </>
          )}
        </div>
      </CardContent>
    </Card>
  );
}
