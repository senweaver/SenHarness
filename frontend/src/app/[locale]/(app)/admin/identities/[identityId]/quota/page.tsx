"use client";

import { use, useEffect, useRef, useState } from "react";
import { Link } from "@/lib/navigation";
import { IconArrowLeft } from "@tabler/icons-react";
import { useTranslations } from "next-intl";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { PageHeader } from "@/components/ui/page-header";
import { Skeleton } from "@/components/ui/skeleton";
import {
  useAdminIdentityQuota,
  useUpdateIdentityQuota,
} from "@/hooks/use-workspace-quota";

export default function AdminIdentityQuotaPage({
  params,
}: {
  params: Promise<{ identityId: string }>;
}) {
  const { identityId } = use(params);
  const t = useTranslations("admin.workspaceQuota");
  const tQuota = useTranslations("settings.workspaceQuota");
  const { data, isLoading, error } = useAdminIdentityQuota(identityId);
  const updateQuota = useUpdateIdentityQuota();

  const [override, setOverride] = useState<string>("");
  const [clearing, setClearing] = useState(false);
  const initialised = useRef<string | null>(null);

  useEffect(() => {
    if (!data) return;
    // Re-sync only when the server-side row identity changes (initial
    // load or after a cache invalidation hands us a different
    // identity row); avoids the cascading-render warning that plain
    // setState-in-effect produces.
    if (initialised.current === data.identity_id) return;
    initialised.current = data.identity_id;
    setOverride(data.override === null ? "" : String(data.override));
  }, [data]);

  const onSave = async () => {
    const trimmed = override.trim();
    if (trimmed === "") {
      toast.error(t("validation.empty"));
      return;
    }
    const n = Number(trimmed);
    if (!Number.isFinite(n) || n < 0 || !Number.isInteger(n)) {
      toast.error(t("validation.invalid"));
      return;
    }
    try {
      await updateQuota.mutateAsync({ identityId, quota: n });
      toast.success(t("saved"));
    } catch {
      toast.error(t("saveFailed"));
    }
  };

  const onClear = async () => {
    try {
      setClearing(true);
      await updateQuota.mutateAsync({ identityId, quota: null });
      toast.success(t("cleared"));
    } catch {
      toast.error(t("saveFailed"));
    } finally {
      setClearing(false);
    }
  };

  if (isLoading) {
    return <Skeleton className="h-64" />;
  }
  if (error || !data) {
    return (
      <div>
        <PageHeader title={t("title")} description={t("loadFailed")} />
        <Link
          href="/admin/users"
          className="mt-4 inline-flex items-center gap-1 text-sm sh-muted hover:underline"
        >
          <IconArrowLeft className="size-4" /> {t("back")}
        </Link>
      </div>
    );
  }

  return (
    <div className="space-y-6" data-testid="admin-identity-quota-page">
      <PageHeader
        title={t("titleFor", { name: data.name, email: data.email })}
        description={t("description")}
      />

      <Card>
        <CardHeader>
          <CardTitle>{t("currentTitle")}</CardTitle>
          <CardDescription>{t("currentDescription")}</CardDescription>
        </CardHeader>
        <CardContent>
          <dl className="grid grid-cols-2 gap-4 text-sm md:grid-cols-3">
            <div>
              <dt className="sh-muted text-[11px] uppercase tracking-wide">
                {t("metric.used")}
              </dt>
              <dd className="text-2xl font-semibold">{data.used}</dd>
            </div>
            <div>
              <dt className="sh-muted text-[11px] uppercase tracking-wide">
                {t("metric.limit")}
              </dt>
              <dd className="text-2xl font-semibold">{data.limit}</dd>
            </div>
            <div>
              <dt className="sh-muted text-[11px] uppercase tracking-wide">
                {t("metric.sourceKind")}
              </dt>
              <dd className="font-mono text-[12px]">
                {tQuota(`sourceKind.${data.source_kind}`)}
              </dd>
            </div>
            <div>
              <dt className="sh-muted text-[11px] uppercase tracking-wide">
                {t("metric.override")}
              </dt>
              <dd className="text-[12px]">
                {data.override === null
                  ? t("metric.overrideNone")
                  : data.override}
              </dd>
            </div>
          </dl>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>{t("overrideFormTitle")}</CardTitle>
          <CardDescription>{t("overrideFormDescription")}</CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="space-y-2">
            <Label htmlFor="quota">{t("overrideField")}</Label>
            <Input
              id="quota"
              type="number"
              min={0}
              value={override}
              onChange={(e) => setOverride(e.target.value)}
              placeholder={String(data.limit)}
              className="max-w-xs"
            />
          </div>
          <div className="flex items-center gap-3">
            <Button
              onClick={onSave}
              disabled={updateQuota.isPending}
            >
              {t("save")}
            </Button>
            <Button
              variant="ghost"
              onClick={onClear}
              disabled={data.override === null || clearing}
            >
              {t("clear")}
            </Button>
          </div>
        </CardContent>
      </Card>
    </div>
  );
}
