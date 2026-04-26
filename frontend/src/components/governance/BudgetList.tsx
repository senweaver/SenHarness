"use client";

/**
 * BudgetList — Card list of budgets with "New" / edit / delete actions.
 *
 * Peer of PolicyList; shares the scope palette + `allowedScopes` gate.
 */

import { useState } from "react";
import { IconPlus, IconCash, IconTrash, IconEdit } from "@tabler/icons-react";
import { useTranslations } from "next-intl";
import { toast } from "sonner";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import {
  type BudgetRead,
  type GovernanceScope,
  useBudgets,
  useDeleteBudget,
} from "@/hooks/use-governance";

import { BudgetForm } from "./BudgetForm";

interface BudgetListProps {
  allowedScopes: GovernanceScope[];
  forceScope?: GovernanceScope;
  filterScopes?: GovernanceScope[];
}

function scopeVariant(scope: GovernanceScope): "primary" | "warning" | "default" {
  if (scope === "global") return "warning";
  if (scope === "agent") return "primary";
  return "default";
}

export function BudgetList({
  allowedScopes,
  forceScope,
  filterScopes,
}: BudgetListProps) {
  const t = useTranslations("settings.governance");
  const tCommon = useTranslations("common");
  const { data, isLoading } = useBudgets();
  const del = useDeleteBudget();

  const [createOpen, setCreateOpen] = useState(false);
  const [editTarget, setEditTarget] = useState<BudgetRead | null>(null);

  const visible = (data ?? []).filter((b) =>
    filterScopes ? filterScopes.includes(b.scope) : true,
  );

  const remove = async (b: BudgetRead) => {
    if (!confirm(t("confirmDelete", { name: b.name }))) return;
    try {
      await del.mutateAsync(b.id);
      toast.success(t("deleted"));
    } catch (err) {
      toast.error(err instanceof Error ? err.message : t("deleteFailed"));
    }
  };

  return (
    <div>
      <div className="mb-3 flex items-center justify-between">
        <div>
          <h3 className="text-sm font-medium">{t("budgetsHeading")}</h3>
          <p className="text-[11px] sh-muted">{t("budgetsHelp")}</p>
        </div>
        <Button
          size="sm"
          onClick={() => setCreateOpen(true)}
          data-testid="budget-new"
        >
          <IconPlus className="size-4" /> {t("newBudget")}
        </Button>
      </div>

      {isLoading && <Skeleton className="h-24" />}

      {!isLoading && visible.length === 0 && (
        <Card>
          <CardContent className="py-10 text-center">
            <IconCash className="mx-auto size-8 sh-muted" />
            <p className="mt-3 text-sm sh-muted">{t("empty")}</p>
          </CardContent>
        </Card>
      )}

      <div className="space-y-2" data-testid="budget-list">
        {visible.map((b) => (
          <Card key={b.id}>
            <CardContent className="flex items-center gap-3 py-3">
              <div className="flex-1 min-w-0">
                <div className="flex items-center gap-2">
                  <Badge variant={scopeVariant(b.scope)}>
                    {t(`scope.${b.scope}`)}
                  </Badge>
                  <Badge variant="outline">{t(`period.${b.period}`)}</Badge>
                  {!b.enabled && (
                    <Badge variant="outline">{tCommon("disabled")}</Badge>
                  )}
                  <span className="truncate text-sm font-medium">{b.name}</span>
                  <span className="ml-auto tabular-nums text-sm font-medium">
                    {b.currency} {b.limit_amount}
                  </span>
                </div>
                <p className="mt-0.5 text-[11px] sh-muted">
                  {t("budgetMeta", {
                    alert: b.alert_threshold_pct ?? 0,
                  })}
                </p>
              </div>
              <div className="flex items-center gap-1">
                <Button
                  variant="ghost"
                  size="sm"
                  onClick={() => setEditTarget(b)}
                >
                  <IconEdit className="size-3.5" />
                  {tCommon("edit")}
                </Button>
                <Button
                  variant="ghost"
                  size="sm"
                  onClick={() => void remove(b)}
                  disabled={del.isPending}
                >
                  <IconTrash className="size-3.5" />
                  {tCommon("delete")}
                </Button>
              </div>
            </CardContent>
          </Card>
        ))}
      </div>

      <BudgetForm
        mode="create"
        open={createOpen}
        onOpenChange={setCreateOpen}
        allowedScopes={allowedScopes}
        forceScope={forceScope}
      />
      {editTarget && (
        <BudgetForm
          mode="edit"
          open={Boolean(editTarget)}
          onOpenChange={(v) => !v && setEditTarget(null)}
          initial={editTarget}
          allowedScopes={allowedScopes}
          forceScope={forceScope}
        />
      )}
    </div>
  );
}
