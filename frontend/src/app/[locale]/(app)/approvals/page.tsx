"use client";

import { useMemo, useState } from "react";
import {
  IconBuildingCommunity,
  IconHistory,
  IconRefresh,
  IconShieldCheck,
} from "@tabler/icons-react";
import { useLocale, useTranslations } from "next-intl";

import { ApprovalCard } from "@/components/approvals/ApprovalCard";
import { BulkDecisionDialog } from "@/components/approvals/BulkDecisionDialog";
import { type DecisionAction } from "@/components/approvals/DecisionDialog";
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
import {
  usePendingApprovals,
  useRecentApprovals,
} from "@/hooks/use-approvals";
import { type PermissionInfo, usePermissions } from "@/hooks/use-permissions";
import { cn, relativeTime } from "@/lib/utils";
import type { ApprovalRead, ApprovalResourceType } from "@/types/api";

type TabKey = "pending" | "history";
type HistoryStatus = "all" | "approved" | "denied" | "expired";
type ResourceFilter = "all" | ApprovalResourceType;

const RESOURCE_FILTER_OPTIONS: ResourceFilter[] = [
  "all",
  "skill_pack_create",
  "skill_pack_patch",
  "skill_pack_edit",
  "skill_pack_delete",
  "skill_pack_archive",
  "skill_pack_write_file",
  "skill_pack_remove_file",
  "flow_create",
];

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
  const [resourceFilter, setResourceFilter] = useState<ResourceFilter>("all");
  const [q, setQ] = useState("");

  const pendingItemsRaw = pending.data ?? [];
  const pendingItems = useMemo(() => {
    const filtered =
      resourceFilter === "all"
        ? pendingItemsRaw
        : pendingItemsRaw.filter(
            (row) => row.resource_type === resourceFilter,
          );
    // Sort by expires_at ASC so the most-urgent rows render first;
    // null expiry is pushed to the end (legacy tool-call rows).
    return [...filtered].sort((a, b) => {
      const ax = a.expires_at ? Date.parse(a.expires_at) : Infinity;
      const bx = b.expires_at ? Date.parse(b.expires_at) : Infinity;
      return ax - bx;
    });
  }, [pendingItemsRaw, resourceFilter]);
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
    const byResource =
      resourceFilter === "all"
        ? byStatus
        : byStatus.filter((r) => r.resource_type === resourceFilter);
    return filterQuery(byResource, q);
  }, [recent.data, statusFilter, resourceFilter, q]);

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
          <ResourceTypeFilter
            value={resourceFilter}
            onChange={setResourceFilter}
          />
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
                <ApprovalCard
                  key={row.id}
                  row={row}
                  locale={locale}
                  selected={visibleSelectedIds.has(row.id)}
                  onToggleSelect={() => toggleOne(row.id)}
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

function ResourceTypeFilter({
  value,
  onChange,
}: {
  value: ResourceFilter;
  onChange: (next: ResourceFilter) => void;
}) {
  const t = useTranslations("approvals.resourceFilter");
  return (
    <Select value={value} onValueChange={(v) => onChange(v as ResourceFilter)}>
      <SelectTrigger className="h-8 w-[180px]">
        <SelectValue />
      </SelectTrigger>
      <SelectContent>
        {RESOURCE_FILTER_OPTIONS.map((opt) => (
          <SelectItem key={opt} value={opt}>
            {t(opt)}
          </SelectItem>
        ))}
      </SelectContent>
    </Select>
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
