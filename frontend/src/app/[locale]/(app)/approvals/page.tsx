"use client";

import { useMemo, useState } from "react";
import {
  IconBuildingCommunity,
  IconCheck,
  IconChevronDown,
  IconHistory,
  IconHourglass,
  IconLock,
  IconRefresh,
  IconShieldCheck,
  IconX,
} from "@tabler/icons-react";
import { useLocale, useTranslations } from "next-intl";

import { BulkDecisionDialog } from "@/components/approvals/BulkDecisionDialog";
import { DecisionDialog, type DecisionAction } from "@/components/approvals/DecisionDialog";
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
import { useCountdown } from "@/hooks/use-countdown";
import {
  usePendingApprovals,
  useRecentApprovals,
} from "@/hooks/use-approvals";
import { type PermissionInfo, usePermissions } from "@/hooks/use-permissions";
import { cn, relativeTime } from "@/lib/utils";
import type { ApprovalRead } from "@/types/api";

type TabKey = "pending" | "history";
type HistoryStatus = "all" | "approved" | "denied" | "expired";

export default function ApprovalsQueuePage() {
  const t = useTranslations("approvals");
  const tBulk = useTranslations("approvals.bulk");
  const tCommon = useTranslations("common");
  const locale = useLocale();
  const perms = usePermissions();
  const pending = usePendingApprovals();
  const recent = useRecentApprovals();

  const [tab, setTab] = useState<TabKey>("pending");
  const [statusFilter, setStatusFilter] = useState<HistoryStatus>("all");
  const [q, setQ] = useState("");

  const pendingItems = pending.data ?? [];
  const pendingFiltered = useMemo(
    () => filterQuery(pendingItems, q),
    [pendingItems, q],
  );

  const historyItems = useMemo(() => {
    const rows = (recent.data ?? []).filter((r) => r.status !== "pending");
    const byStatus =
      statusFilter === "all"
        ? rows
        : rows.filter((r) => r.status === statusFilter);
    return filterQuery(byStatus, q);
  }, [recent.data, statusFilter, q]);

  // ── Selection (pending-only, filter-aware) ─────────────────────────
  // We key selection on id and prune entries that leave the visible list so
  // a row decided elsewhere (WS push) doesn't stay stuck selected.
  const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set());
  const selectableIds = useMemo(
    () =>
      pendingFiltered
        .filter((r) => canDecide(perms, r))
        .map((r) => r.id),
    [pendingFiltered, perms],
  );
  // Prune selections that no longer appear in the visible list.
  const visibleSelectedIds = useMemo(() => {
    const visible = new Set(selectableIds);
    const out = new Set<string>();
    selectedIds.forEach((id) => {
      if (visible.has(id)) out.add(id);
    });
    return out;
  }, [selectedIds, selectableIds]);
  const toggleOne = (id: string) => {
    setSelectedIds((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };
  const toggleAll = (on: boolean) => {
    if (on) {
      setSelectedIds(new Set(selectableIds));
    } else {
      setSelectedIds(new Set());
    }
  };
  const clearSelection = () => setSelectedIds(new Set());

  const [bulkDialog, setBulkDialog] = useState<{
    open: boolean;
    action: DecisionAction;
  }>({ open: false, action: "approve" });
  const openBulk = (action: DecisionAction) =>
    setBulkDialog({ open: true, action });

  const allSelected =
    selectableIds.length > 0 &&
    visibleSelectedIds.size === selectableIds.length;
  const someSelected =
    visibleSelectedIds.size > 0 &&
    visibleSelectedIds.size < selectableIds.length;

  return (
    <div className="p-6">
      <PageHeader
        title={t("title")}
        description={t("description")}
        actions={
          <Button
            variant="outline"
            size="sm"
            disabled={pending.isFetching || recent.isFetching}
            onClick={() => {
              pending.refetch();
              recent.refetch();
            }}
          >
            <IconRefresh
              className={cn(
                "size-4",
                (pending.isFetching || recent.isFetching) && "animate-spin",
              )}
            />
            {tCommon("refresh")}
          </Button>
        }
      />

      <div className="mb-4 flex flex-wrap items-center gap-2 border-b">
        <TabButton
          active={tab === "pending"}
          onClick={() => setTab("pending")}
          icon={<IconShieldCheck className="size-4" />}
          label={t("tabPending")}
          badge={pendingItems.length}
        />
        <TabButton
          active={tab === "history"}
          onClick={() => setTab("history")}
          icon={<IconHistory className="size-4" />}
          label={t("tabHistory")}
        />
        <div className="ml-auto flex items-center gap-2 pb-2">
          {tab === "history" && (
            <Select
              value={statusFilter}
              onValueChange={(v) => setStatusFilter(v as HistoryStatus)}
            >
              <SelectTrigger className="h-8 w-[140px]">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="all">{t("filter.allStatuses")}</SelectItem>
                <SelectItem value="approved">{t("status.approved")}</SelectItem>
                <SelectItem value="denied">{t("status.denied")}</SelectItem>
                <SelectItem value="expired">{t("status.expired")}</SelectItem>
              </SelectContent>
            </Select>
          )}
          <Input
            value={q}
            onChange={(e) => setQ(e.target.value)}
            placeholder={t("searchPlaceholder")}
            className="h-8 w-[200px]"
          />
        </div>
      </div>

      {tab === "pending" ? (
        <Card>
          <CardHeader className="flex flex-row items-center justify-between gap-2 space-y-0">
            <div className="flex items-center gap-2">
              <TriStateCheckbox
                all={allSelected}
                some={someSelected}
                disabled={selectableIds.length === 0}
                onChange={toggleAll}
                aria-label={tBulk("selectAllAria")}
              />
              <CardTitle className="text-sm">
                {t("pendingCount", { n: pendingItems.length })}
              </CardTitle>
            </div>
            {visibleSelectedIds.size > 0 && (
              <div className="flex items-center gap-1.5 rounded-md border bg-amber-50/60 px-2 py-1 text-xs dark:bg-amber-950/20">
                <Badge variant="default" className="bg-amber-500 text-white">
                  {tBulk("selectedBadge", { n: visibleSelectedIds.size })}
                </Badge>
                <Button
                  size="sm"
                  className="h-7 bg-emerald-600 hover:bg-emerald-700"
                  onClick={() => openBulk("approve")}
                >
                  <IconCheck className="size-3" />
                  {tBulk("bulkApprove")}
                </Button>
                <Button
                  size="sm"
                  variant="destructive"
                  className="h-7"
                  onClick={() => openBulk("deny")}
                >
                  <IconX className="size-3" />
                  {tBulk("bulkDeny")}
                </Button>
                <Button
                  size="sm"
                  variant="ghost"
                  className="h-7"
                  onClick={clearSelection}
                >
                  {tBulk("clearSelection")}
                </Button>
              </div>
            )}
          </CardHeader>
          <CardContent className="space-y-2">
            {pending.isLoading ? (
              <Skeleton className="h-20" />
            ) : pendingFiltered.length === 0 ? (
              <p className="py-8 text-center text-xs sh-muted">
                {t("emptyPending")}
              </p>
            ) : (
              pendingFiltered.map((row) => (
                <PendingCard
                  key={row.id}
                  row={row}
                  locale={locale}
                  selected={visibleSelectedIds.has(row.id)}
                  onToggle={() => toggleOne(row.id)}
                />
              ))
            )}
          </CardContent>
          <BulkDecisionDialog
            approvalIds={Array.from(visibleSelectedIds)}
            action={bulkDialog.action}
            open={bulkDialog.open}
            onOpenChange={(o) =>
              setBulkDialog((prev) => ({ ...prev, open: o }))
            }
            onDone={() => clearSelection()}
          />
        </Card>
      ) : (
        <Card>
          <CardHeader>
            <CardTitle className="text-sm">
              {t("historyCount", { n: historyItems.length })}
            </CardTitle>
          </CardHeader>
          <CardContent>
            {recent.isLoading ? (
              <Skeleton className="h-40" />
            ) : historyItems.length === 0 ? (
              <p className="py-8 text-center text-xs sh-muted">
                {t("emptyHistory")}
              </p>
            ) : (
              <div className="divide-y">
                {historyItems.map((row) => (
                  <HistoryRow key={row.id} row={row} locale={locale} />
                ))}
              </div>
            )}
          </CardContent>
        </Card>
      )}
    </div>
  );
}

function TabButton({
  active,
  onClick,
  icon,
  label,
  badge,
}: {
  active: boolean;
  onClick: () => void;
  icon: React.ReactNode;
  label: string;
  badge?: number;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={cn(
        "-mb-px flex items-center gap-1.5 border-b-2 px-3 py-2 text-sm font-medium transition-colors",
        active
          ? "border-[rgb(var(--color-primary))] text-[rgb(var(--color-primary))]"
          : "border-transparent sh-muted hover:text-foreground",
      )}
    >
      {icon}
      {label}
      {typeof badge === "number" && badge > 0 && (
        <Badge variant="default" className="ml-1 h-5 px-1.5 text-[10px]">
          {badge}
        </Badge>
      )}
    </button>
  );
}

function PendingCard({
  row,
  locale,
  selected,
  onToggle,
}: {
  row: ApprovalRead;
  locale: string;
  selected: boolean;
  onToggle: () => void;
}) {
  const t = useTranslations("approvals");
  const tBulk = useTranslations("approvals.bulk");
  const perms = usePermissions();
  const { label: countdownLabel, totalMs, expired } = useCountdown(
    row.expires_at,
  );
  const [expanded, setExpanded] = useState(false);
  const [dialog, setDialog] = useState<{
    open: boolean;
    action: DecisionAction;
  }>({ open: false, action: "approve" });

  const canDecide = perms.canDecideApproval({
    requestedByIdentityId: row.requested_by_identity_id,
  });
  // Last-60s red warning; 60-120s orange; otherwise neutral.
  const urgency: "red" | "amber" | "neutral" =
    expired || totalMs <= 0
      ? "red"
      : totalMs <= 60_000
        ? "red"
        : totalMs <= 120_000
          ? "amber"
          : "neutral";

  const countdownClass = cn(
    "flex items-center gap-1 font-mono tabular-nums text-xs",
    urgency === "red" && "text-rose-600 dark:text-rose-400",
    urgency === "amber" && "text-amber-600 dark:text-amber-400",
    urgency === "neutral" && "sh-muted",
  );

  return (
    <>
      <div
        className={cn(
          "rounded-md border p-3 transition-colors",
          selected
            ? "border-amber-500 bg-amber-100/60 dark:border-amber-500 dark:bg-amber-900/30"
            : "border-amber-300 bg-amber-50/40 dark:border-amber-700 dark:bg-amber-950/20",
        )}
      >
        <div className="flex flex-wrap items-center gap-2">
          {canDecide && (
            <input
              type="checkbox"
              checked={selected}
              onChange={onToggle}
              aria-label={tBulk("selectRowAria", { tool: row.tool_name })}
              className="size-3.5 accent-amber-500"
            />
          )}
          <Badge variant="outline" className="font-mono text-[11px]">
            {row.tool_name}
          </Badge>
          <span className="text-[11px] sh-muted">
            {relativeTime(row.created_at, locale)}
          </span>
          {row.requester_department_name && (
            <Badge variant="outline" className="gap-1 text-[10px]">
              <IconBuildingCommunity className="size-3" />
              {row.requester_department_name}
            </Badge>
          )}
          {row.expires_at && (
            <span className={countdownClass} title={row.expires_at}>
              <IconHourglass className="size-3" />
              {expired ? t("expiredLabel") : countdownLabel}
            </span>
          )}
        </div>

        {row.summary && (
          <div className="mt-2 break-all font-mono text-[12px]">
            {row.summary}
          </div>
        )}

        <button
          type="button"
          className="mt-2 flex items-center gap-1 text-[11px] sh-muted hover:underline"
          onClick={() => setExpanded((e) => !e)}
        >
          <IconChevronDown
            className={cn(
              "size-3 transition-transform",
              expanded && "rotate-180",
            )}
          />
          {expanded ? t("collapseArgs") : t("expandArgs")}
        </button>
        {expanded && (
          <pre className="mt-1 overflow-x-auto rounded bg-black/5 p-2 text-[10.5px] dark:bg-white/5">
            {JSON.stringify(row.tool_args, null, 2)}
          </pre>
        )}

        <div className="mt-3 flex flex-wrap items-center gap-2">
          {canDecide ? (
            <>
              <Button
                size="sm"
                className="h-7 bg-emerald-600 hover:bg-emerald-700"
                onClick={() => setDialog({ open: true, action: "approve" })}
              >
                <IconCheck className="size-3" />
                {t("approve")}
              </Button>
              <Button
                size="sm"
                variant="destructive"
                className="h-7"
                onClick={() => setDialog({ open: true, action: "deny" })}
              >
                <IconX className="size-3" />
                {t("deny")}
              </Button>
            </>
          ) : (
            <Badge variant="outline" className="gap-1">
              <IconLock className="size-3" />
              {t("noPermission")}
            </Badge>
          )}
        </div>
      </div>

      <DecisionDialog
        approvalId={row.id}
        action={dialog.action}
        summary={row.summary}
        open={dialog.open}
        onOpenChange={(o) => setDialog((d) => ({ ...d, open: o }))}
      />
    </>
  );
}

function HistoryRow({
  row,
  locale,
}: {
  row: ApprovalRead;
  locale: string;
}) {
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
      case "cancelled":
        return <Badge variant="outline">{row.status}</Badge>;
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
        <div className="mt-0.5 flex flex-wrap items-center gap-1.5 text-[11px] sh-muted">
          {row.requester_department_name && (
            <Badge variant="outline" className="gap-1 text-[10px]">
              <IconBuildingCommunity className="size-3" />
              {row.requester_department_name}
            </Badge>
          )}
          {row.decided_by_department_name &&
            row.decided_by_department_name !== row.requester_department_name && (
              <span>
                → {row.decided_by_department_name}
              </span>
            )}
          {row.decided_reason && (
            <span className="break-words">
              · {t("reasonLabel")}: {row.decided_reason}
            </span>
          )}
        </div>
      </div>
      <div className="flex justify-end">{statusBadge}</div>
    </div>
  );
}

function filterQuery(rows: ApprovalRead[], q: string): ApprovalRead[] {
  const needle = q.trim().toLowerCase();
  if (!needle) return rows;
  return rows.filter(
    (r) =>
      r.tool_name.toLowerCase().includes(needle) ||
      (r.summary ?? "").toLowerCase().includes(needle) ||
      JSON.stringify(r.tool_args).toLowerCase().includes(needle),
  );
}

/** Client-side predicate mirroring the server-side decide rule. */
function canDecide(perms: PermissionInfo, row: ApprovalRead): boolean {
  return perms.canDecideApproval({
    requestedByIdentityId: row.requested_by_identity_id,
  });
}

/**
 * `TriStateCheckbox` — HTML checkbox with the three UX states (none / some /
 * all). The indeterminate flag has to be toggled via ref because React
 * doesn't reflect it through props.
 */
function TriStateCheckbox({
  all,
  some,
  disabled,
  onChange,
  ...aria
}: {
  all: boolean;
  some: boolean;
  disabled?: boolean;
  onChange: (checked: boolean) => void;
} & React.AriaAttributes) {
  const ref = (node: HTMLInputElement | null) => {
    if (node) node.indeterminate = !all && some;
  };
  return (
    <input
      type="checkbox"
      ref={ref}
      checked={all}
      disabled={disabled}
      onChange={(e) => onChange(e.target.checked)}
      className="size-3.5 accent-amber-500"
      {...aria}
    />
  );
}
