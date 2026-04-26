"use client";

/**
 * PolicyList — Card list of policies with "New" / edit / delete actions.
 *
 * Shown in both the admin (`/admin/governance`) and workspace
 * (`/settings/workspace/governance`) pages. The scope palette and the
 * `allowedScopes` gate are passed in, so the same list works for both
 * platform-admin (GLOBAL) and workspace-admin (WORKSPACE + AGENT) views.
 */

import { useState } from "react";
import { IconPlus, IconShield, IconTrash, IconEdit } from "@tabler/icons-react";
import { useTranslations } from "next-intl";
import { toast } from "sonner";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import {
  type GovernanceScope,
  type PolicyRead,
  useDeletePolicy,
  usePolicies,
} from "@/hooks/use-governance";

import { PolicyForm } from "./PolicyForm";

interface PolicyListProps {
  allowedScopes: GovernanceScope[];
  forceScope?: GovernanceScope;
  /** Only show rows matching these scopes (defaults to allowedScopes). */
  filterScopes?: GovernanceScope[];
}

function scopeVariant(scope: GovernanceScope): "primary" | "warning" | "default" {
  if (scope === "global") return "warning";
  if (scope === "agent") return "primary";
  return "default";
}

export function PolicyList({
  allowedScopes,
  forceScope,
  filterScopes,
}: PolicyListProps) {
  const t = useTranslations("settings.governance");
  const tCommon = useTranslations("common");
  const { data, isLoading } = usePolicies();
  const del = useDeletePolicy();

  const [createOpen, setCreateOpen] = useState(false);
  const [editTarget, setEditTarget] = useState<PolicyRead | null>(null);

  const visible = (data ?? []).filter((p) =>
    filterScopes ? filterScopes.includes(p.scope) : true,
  );

  const remove = async (policy: PolicyRead) => {
    if (!confirm(t("confirmDelete", { name: policy.name }))) return;
    try {
      await del.mutateAsync(policy.id);
      toast.success(t("deleted"));
    } catch (err) {
      toast.error(err instanceof Error ? err.message : t("deleteFailed"));
    }
  };

  return (
    <div>
      <div className="mb-3 flex items-center justify-between">
        <div>
          <h3 className="text-sm font-medium">{t("policiesHeading")}</h3>
          <p className="text-[11px] sh-muted">{t("policiesHelp")}</p>
        </div>
        <Button
          size="sm"
          onClick={() => setCreateOpen(true)}
          data-testid="policy-new"
        >
          <IconPlus className="size-4" /> {t("newPolicy")}
        </Button>
      </div>

      {isLoading && <Skeleton className="h-24" />}

      {!isLoading && visible.length === 0 && (
        <Card>
          <CardContent className="py-10 text-center">
            <IconShield className="mx-auto size-8 sh-muted" />
            <p className="mt-3 text-sm sh-muted">{t("empty")}</p>
          </CardContent>
        </Card>
      )}

      <div className="space-y-2" data-testid="policy-list">
        {visible.map((p) => (
          <Card key={p.id}>
            <CardContent className="flex items-center gap-3 py-3">
              <div className="flex-1 min-w-0">
                <div className="flex items-center gap-2">
                  <Badge variant={scopeVariant(p.scope)}>{t(`scope.${p.scope}`)}</Badge>
                  {!p.enabled && (
                    <Badge variant="outline">{tCommon("disabled")}</Badge>
                  )}
                  <span className="truncate text-sm font-medium">{p.name}</span>
                  <span className="ml-auto text-[10px] sh-muted">
                    {t("priorityLabel", { n: p.priority })}
                  </span>
                </div>
                {p.description && (
                  <p className="mt-0.5 line-clamp-2 text-[12px] sh-muted">
                    {p.description}
                  </p>
                )}
              </div>
              <div className="flex items-center gap-1">
                <Button
                  variant="ghost"
                  size="sm"
                  onClick={() => setEditTarget(p)}
                >
                  <IconEdit className="size-3.5" />
                  {tCommon("edit")}
                </Button>
                <Button
                  variant="ghost"
                  size="sm"
                  onClick={() => void remove(p)}
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

      <PolicyForm
        mode="create"
        open={createOpen}
        onOpenChange={setCreateOpen}
        allowedScopes={allowedScopes}
        forceScope={forceScope}
      />
      {editTarget && (
        <PolicyForm
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
