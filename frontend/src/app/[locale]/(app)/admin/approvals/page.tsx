"use client";

import { useMemo, useState } from "react";
import { Link } from "@/lib/navigation";
import {
  IconBuildingCommunity,
  IconCheck,
  IconChevronDown,
  IconHourglass,
  IconRefresh,
  IconX,
} from "@tabler/icons-react";
import { useLocale, useTranslations } from "next-intl";
import { toast } from "sonner";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { PageHeader } from "@/components/ui/page-header";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";
import { Textarea } from "@/components/ui/textarea";
import { useCountdown } from "@/hooks/use-countdown";
import {
  type AdminApprovalRow,
  type AdminApprovalStatusFilter,
  useAdminApprovals,
  useAdminDecideApproval,
} from "@/hooks/use-admin";
import { cn, relativeTime } from "@/lib/utils";

export default function AdminApprovalsPage() {
  const t = useTranslations("admin.approvals");
  const tCommon = useTranslations("common");
  const locale = useLocale();

  const [statusFilter, setStatusFilter] =
    useState<AdminApprovalStatusFilter>("pending");
  const [q, setQ] = useState("");

  const { data, isLoading, isFetching, refetch } = useAdminApprovals({
    status: statusFilter,
    limit: 200,
  });

  // Group by workspace so admins scanning runaway tenants can act top-down.
  const groups = useMemo(() => {
    const rows = (data ?? []).filter((r) => {
      if (!q.trim()) return true;
      const needle = q.trim().toLowerCase();
      return (
        r.tool_name.toLowerCase().includes(needle) ||
        (r.summary ?? "").toLowerCase().includes(needle) ||
        (r.workspace_name ?? "").toLowerCase().includes(needle) ||
        (r.workspace_slug ?? "").toLowerCase().includes(needle) ||
        (r.requester_name ?? "").toLowerCase().includes(needle) ||
        (r.requester_email ?? "").toLowerCase().includes(needle)
      );
    });
    const by = new Map<string, { name: string; slug: string; rows: AdminApprovalRow[] }>();
    for (const row of rows) {
      const key = row.workspace_id;
      const g = by.get(key) ?? {
        name: row.workspace_name ?? "—",
        slug: row.workspace_slug ?? "",
        rows: [],
      };
      g.rows.push(row);
      by.set(key, g);
    }
    return Array.from(by.entries()).map(([id, v]) => ({ id, ...v }));
  }, [data, q]);

  return (
    <div>
      <PageHeader
        title={t("title")}
        description={t("description")}
        actions={
          <Button
            variant="outline"
            size="sm"
            onClick={() => refetch()}
            disabled={isFetching}
          >
            <IconRefresh className={cn("size-4", isFetching && "animate-spin")} />
            {tCommon("refresh")}
          </Button>
        }
      />

      <Card className="mb-3">
        <CardContent className="grid gap-3 py-3 sm:grid-cols-2">
          <div>
            <label className="text-[11px] sh-muted">{t("filter.status")}</label>
            <Select
              value={statusFilter}
              onValueChange={(v) => setStatusFilter(v as AdminApprovalStatusFilter)}
            >
              <SelectTrigger>
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="pending">{t("status.pending")}</SelectItem>
                <SelectItem value="approved">{t("status.approved")}</SelectItem>
                <SelectItem value="denied">{t("status.denied")}</SelectItem>
                <SelectItem value="expired">{t("status.expired")}</SelectItem>
                <SelectItem value="cancelled">{t("status.cancelled")}</SelectItem>
                <SelectItem value="all">{t("filter.all")}</SelectItem>
              </SelectContent>
            </Select>
          </div>
          <div>
            <label className="text-[11px] sh-muted">{t("filter.search")}</label>
            <Input
              value={q}
              onChange={(e) => setQ(e.target.value)}
              placeholder={t("searchPlaceholder")}
            />
          </div>
        </CardContent>
      </Card>

      {isLoading ? (
        <Skeleton className="h-40" />
      ) : groups.length === 0 ? (
        <Card>
          <CardContent className="py-10 text-center text-xs sh-muted">
            {t("empty")}
          </CardContent>
        </Card>
      ) : (
        <div className="space-y-3">
          {groups.map((g) => (
            <Card key={g.id}>
              <CardHeader className="pb-2">
                <CardTitle className="flex items-center gap-2 text-sm">
                  <IconBuildingCommunity className="size-4 sh-muted" />
                  <span>{g.name}</span>
                  <span className="font-mono text-[11px] sh-muted">
                    {g.slug}
                  </span>
                  <Badge variant="outline" className="ml-auto">
                    {t("countInGroup", { n: g.rows.length })}
                  </Badge>
                </CardTitle>
              </CardHeader>
              <CardContent className="divide-y">
                {g.rows.map((row) => (
                  <AdminRow key={row.id} row={row} locale={locale} />
                ))}
              </CardContent>
            </Card>
          ))}
        </div>
      )}
    </div>
  );
}

function AdminRow({
  row,
  locale,
}: {
  row: AdminApprovalRow;
  locale: string;
}) {
  const t = useTranslations("admin.approvals");
  const { label: countdownLabel, totalMs, expired } = useCountdown(row.expires_at);
  const [expanded, setExpanded] = useState(false);
  const [denyOpen, setDenyOpen] = useState(false);
  const [reason, setReason] = useState("");
  const decide = useAdminDecideApproval();

  const isPending = row.status === "pending";
  const urgency: "red" | "amber" | "neutral" =
    !row.expires_at
      ? "neutral"
      : expired || totalMs <= 60_000
        ? "red"
        : totalMs <= 120_000
          ? "amber"
          : "neutral";

  const countdownCn = cn(
    "flex items-center gap-1 font-mono tabular-nums text-[11px]",
    urgency === "red" && "text-rose-600 dark:text-rose-400",
    urgency === "amber" && "text-amber-600 dark:text-amber-400",
    urgency === "neutral" && "sh-muted",
  );

  const approve = async () => {
    try {
      await decide.mutateAsync({ approvalId: row.id, action: "approve" });
      toast.success(t("approvedToast"));
    } catch {
      toast.error(t("failedToast"));
    }
  };
  const deny = async () => {
    if (reason.trim().length < 3) return;
    try {
      await decide.mutateAsync({
        approvalId: row.id,
        action: "deny",
        reason: reason.trim(),
      });
      toast.success(t("deniedToast"));
      setDenyOpen(false);
      setReason("");
    } catch {
      toast.error(t("failedToast"));
    }
  };

  return (
    <div className="py-2 text-xs">
      <div className="flex flex-wrap items-center gap-2">
        <Badge variant="outline" className="font-mono">
          {row.tool_name}
        </Badge>
        <span className="sh-muted">
          {relativeTime(row.created_at, locale)}
        </span>
        {row.requester_name && (
          <span className="sh-muted">
            · {row.requester_name}
            {row.requester_email ? ` <${row.requester_email}>` : ""}
          </span>
        )}
        {row.requester_department_name && (
          <Badge variant="outline" className="gap-1 text-[10px]">
            <IconBuildingCommunity className="size-3" />
            {row.requester_department_name}
          </Badge>
        )}
        {isPending && row.expires_at && (
          <span className={countdownCn} title={row.expires_at}>
            <IconHourglass className="size-3" />
            {expired ? t("expired") : countdownLabel}
          </span>
        )}
        <StatusBadge status={row.status} />
      </div>

      {row.summary && (
        <div className="mt-1 break-all font-mono text-[11.5px]">{row.summary}</div>
      )}

      <button
        type="button"
        className="mt-1 flex items-center gap-1 text-[11px] sh-muted hover:underline"
        onClick={() => setExpanded((e) => !e)}
      >
        <IconChevronDown
          className={cn("size-3 transition-transform", expanded && "rotate-180")}
        />
        {expanded ? t("collapseArgs") : t("expandArgs")}
      </button>
      {expanded && (
        <pre className="mt-1 overflow-x-auto rounded bg-black/5 p-2 text-[10.5px] dark:bg-white/5">
          {JSON.stringify(row.tool_args, null, 2)}
        </pre>
      )}

      {isPending ? (
        <div className="mt-2 flex gap-2">
          <Button
            size="sm"
            className="h-7 bg-emerald-600 hover:bg-emerald-700"
            onClick={approve}
            disabled={decide.isPending}
          >
            <IconCheck className="size-3" />
            {t("approve")}
          </Button>
          <Button
            size="sm"
            variant="destructive"
            className="h-7"
            onClick={() => setDenyOpen(true)}
          >
            <IconX className="size-3" />
            {t("deny")}
          </Button>
          <Button
            size="sm"
            variant="ghost"
            className="h-7 ml-auto"
            asChild
          >
            <Link href={`/chat/${row.session_id}`}>{t("openSession")}</Link>
          </Button>
        </div>
      ) : (
        row.decided_reason && (
          <div className="mt-1 break-words text-[11px] sh-muted">
            {t("reason")}: {row.decided_reason}
          </div>
        )
      )}

      <Dialog open={denyOpen} onOpenChange={setDenyOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>{t("denyTitle")}</DialogTitle>
            <DialogDescription>{t("denyDescription")}</DialogDescription>
          </DialogHeader>
          <div className="grid gap-1.5">
            <Label htmlFor={`deny-${row.id}`}>{t("reasonLabel")}</Label>
            <Textarea
              id={`deny-${row.id}`}
              value={reason}
              onChange={(e) => setReason(e.target.value)}
              className="min-h-[96px]"
              maxLength={500}
              autoFocus
            />
          </div>
          <DialogFooter>
            <Button variant="ghost" onClick={() => setDenyOpen(false)}>
              {t("cancel")}
            </Button>
            <Button
              variant="destructive"
              onClick={deny}
              disabled={decide.isPending || reason.trim().length < 3}
            >
              {t("confirmDeny")}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}

function StatusBadge({ status }: { status: AdminApprovalRow["status"] }) {
  const t = useTranslations("admin.approvals.status");
  switch (status) {
    case "pending":
      return <Badge variant="warning">{t("pending")}</Badge>;
    case "approved":
      return (
        <Badge variant="default" className="bg-emerald-600 text-white">
          {t("approved")}
        </Badge>
      );
    case "denied":
      return <Badge variant="danger">{t("denied")}</Badge>;
    case "expired":
      return <Badge variant="outline">{t("expired")}</Badge>;
    case "cancelled":
      return <Badge variant="outline">{t("cancelled")}</Badge>;
    default:
      return <Badge variant="outline">{status}</Badge>;
  }
}
