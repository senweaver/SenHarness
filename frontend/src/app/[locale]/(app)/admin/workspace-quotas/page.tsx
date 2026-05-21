"use client";

import { useMemo, useState } from "react";
import { Link } from "@/lib/navigation";
import { useTranslations } from "next-intl";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { PageHeader } from "@/components/ui/page-header";
import { Skeleton } from "@/components/ui/skeleton";
import {
  useAdminQuotaList,
  useUpdateIdentityQuota,
} from "@/hooks/use-workspace-quota";
import type { AdminWorkspaceQuotaRow } from "@/types/api";

export default function AdminWorkspaceQuotasPage() {
  const t = useTranslations("admin.workspaceQuotaList");
  const tQuota = useTranslations("settings.workspaceQuota");
  const { data, isLoading } = useAdminQuotaList({ limit: 200 });
  const update = useUpdateIdentityQuota();
  const [bulkValue, setBulkValue] = useState("5");
  const [selected, setSelected] = useState<Set<string>>(new Set());

  const rows = useMemo(() => data?.rows ?? [], [data]);

  const toggle = (id: string) => {
    const next = new Set(selected);
    if (next.has(id)) next.delete(id);
    else next.add(id);
    setSelected(next);
  };

  const onBulkRaise = async () => {
    const n = Number(bulkValue);
    if (!Number.isInteger(n) || n < 0) {
      toast.error(t("validation.invalid"));
      return;
    }
    if (selected.size === 0) {
      toast.error(t("validation.selectNone"));
      return;
    }
    let ok = 0;
    for (const id of selected) {
      try {
        await update.mutateAsync({ identityId: id, quota: n });
        ok += 1;
      } catch {
        // continue best-effort
      }
    }
    toast.success(t("bulkRaised", { count: ok }));
    setSelected(new Set());
  };

  return (
    <div className="space-y-6">
      <PageHeader title={t("title")} description={t("description")} />

      <Card>
        <CardHeader>
          <CardTitle>{t("bulkTitle")}</CardTitle>
        </CardHeader>
        <CardContent>
          <div className="flex flex-wrap items-end gap-3">
            <div className="space-y-1">
              <label className="text-[12px] sh-muted">
                {t("bulkValueLabel")}
              </label>
              <Input
                type="number"
                min={0}
                value={bulkValue}
                onChange={(e) => setBulkValue(e.target.value)}
                className="w-32"
              />
            </div>
            <Button
              onClick={onBulkRaise}
              disabled={selected.size === 0 || update.isPending}
            >
              {t("bulkApply", { count: selected.size })}
            </Button>
          </div>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>{t("listTitle")}</CardTitle>
        </CardHeader>
        <CardContent>
          {isLoading ? (
            <Skeleton className="h-48" />
          ) : (
            <table className="w-full border-collapse text-sm">
              <thead>
                <tr className="border-b text-left">
                  <th className="w-8 px-2 py-2"></th>
                  <th className="px-2 py-2">{t("col.identity")}</th>
                  <th className="px-2 py-2">{t("col.source")}</th>
                  <th className="px-2 py-2">{t("col.used")}</th>
                  <th className="px-2 py-2">{t("col.limit")}</th>
                  <th className="px-2 py-2">{t("col.override")}</th>
                  <th className="px-2 py-2 text-right">{t("col.actions")}</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((r: AdminWorkspaceQuotaRow) => (
                  <tr key={r.identity_id} className="border-b last:border-b-0">
                    <td className="px-2 py-2">
                      <input
                        type="checkbox"
                        checked={selected.has(r.identity_id)}
                        onChange={() => toggle(r.identity_id)}
                      />
                    </td>
                    <td className="px-2 py-2">
                      <div className="flex flex-col">
                        <span className="font-medium">{r.name}</span>
                        <span className="text-[11px] sh-muted">{r.email}</span>
                      </div>
                    </td>
                    <td className="px-2 py-2 font-mono text-[12px]">
                      {tQuota(`sourceKind.${r.source_kind}`)}
                    </td>
                    <td className="px-2 py-2">{r.used}</td>
                    <td className="px-2 py-2">{r.limit}</td>
                    <td className="px-2 py-2">
                      {r.override === null ? "—" : r.override}
                    </td>
                    <td className="px-2 py-2 text-right">
                      <Link
                        href={`/admin/identities/${r.identity_id}/quota`}
                        className="text-[12px] underline"
                      >
                        {t("edit")}
                      </Link>
                    </td>
                  </tr>
                ))}
                {rows.length === 0 && (
                  <tr>
                    <td colSpan={7} className="py-4 text-center sh-muted">
                      {t("empty")}
                    </td>
                  </tr>
                )}
              </tbody>
            </table>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
