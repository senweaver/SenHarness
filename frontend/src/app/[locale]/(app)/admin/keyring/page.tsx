"use client";

import { useState } from "react";
import { useTranslations } from "next-intl";
import { toast } from "sonner";
import { IconKey, IconLoader2, IconRefresh, IconShieldLock } from "@tabler/icons-react";

import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { PageHeader } from "@/components/ui/page-header";
import {
  useKeyringStatus,
  useRotateKek,
  type KeyringRotateResult,
} from "@/hooks/use-keyring";

const PROVIDER_LABELS: Record<string, string> = {
  env: "Env (SENHARNESS_MASTER_KEY)",
  file: "File (local JWK set)",
  passphrase: "Passphrase (startup unseal)",
  aws_kms: "AWS KMS",
  gcp_kms: "Google Cloud KMS",
  azure_kv: "Azure Key Vault",
  vault: "HashiCorp Vault Transit",
  hsm: "HSM (PKCS#11)",
};

export default function AdminKeyringPage() {
  const t = useTranslations("admin.keyring");
  const { data, isLoading } = useKeyringStatus();
  const rotate = useRotateKek();
  const [lastResult, setLastResult] = useState<KeyringRotateResult | null>(null);

  const providerLabel = data
    ? PROVIDER_LABELS[data.provider] ?? data.provider
    : "";
  const coveragePct =
    data && data.vault_items_total > 0
      ? Math.round(
          (data.vault_items_on_current_kek / data.vault_items_total) * 100,
        )
      : 100;

  return (
    <div>
      <PageHeader title={t("title")} description={t("description")} />

      {isLoading && <Skeleton className="h-40" />}

      {data && (
        <div className="grid gap-3 sm:grid-cols-2">
          <Card>
            <CardHeader>
              <CardTitle className="flex items-center gap-2 text-base">
                <IconShieldLock className="size-4" />
                {t("providerCard")}
              </CardTitle>
              <CardDescription>{providerLabel}</CardDescription>
            </CardHeader>
            <CardContent>
              <div className="space-y-2 text-[13px]">
                <Row label={t("kekVersion")}>
                  <code className="rounded bg-black/5 px-1.5 py-0.5 font-mono text-[12px] dark:bg-white/10">
                    {data.current_kek_version}
                  </code>
                </Row>
                <Row label={t("rotationSupported")}>
                  {data.rotation_supported ? (
                    <Badge variant="primary">{t("rotationYes")}</Badge>
                  ) : (
                    <Badge variant="warning">{t("rotationManual")}</Badge>
                  )}
                </Row>
              </div>
            </CardContent>
          </Card>

          <Card>
            <CardHeader>
              <CardTitle className="flex items-center gap-2 text-base">
                <IconKey className="size-4" />
                {t("coverageCard")}
              </CardTitle>
              <CardDescription>{t("coverageHint")}</CardDescription>
            </CardHeader>
            <CardContent>
              <div className="space-y-2 text-[13px]">
                <Row label={t("itemsTotal")}>
                  <span className="font-medium">{data.vault_items_total}</span>
                </Row>
                <Row label={t("itemsOnCurrent")}>
                  <span className="font-medium">
                    {data.vault_items_on_current_kek}
                  </span>
                </Row>
                <Row label={t("coverage")}>
                  <span className="font-medium">{coveragePct}%</span>
                </Row>
              </div>
            </CardContent>
          </Card>
        </div>
      )}

      <Card className="mt-4">
        <CardHeader>
          <CardTitle className="text-base">{t("rotateCard")}</CardTitle>
          <CardDescription>{t("rotateDescription")}</CardDescription>
        </CardHeader>
        <CardContent className="space-y-3">
          <div className="flex items-center gap-3">
            <Button
              onClick={async () => {
                if (!confirm(t("confirmRotate"))) return;
                try {
                  const result = await rotate.mutateAsync();
                  setLastResult(result);
                  toast.success(
                    t("rotateSuccess", {
                      rewrapped: result.rewrapped_count,
                    }),
                  );
                } catch (err) {
                  const msg = err instanceof Error ? err.message : t("rotateFailed");
                  toast.error(msg);
                }
              }}
              disabled={
                rotate.isPending || (data && !data.rotation_supported)
              }
            >
              {rotate.isPending ? (
                <IconLoader2 className="size-4 animate-spin" />
              ) : (
                <IconRefresh className="size-4" />
              )}
              {t("rotateButton")}
            </Button>
            {data && !data.rotation_supported && (
              <span className="text-[12px] sh-muted">
                {t("rotateManualHint")}
              </span>
            )}
          </div>

          {lastResult && (
            <div className="rounded-md border bg-black/5 p-3 text-[12px] dark:bg-white/5">
              <div className="mb-1 text-[11px] sh-muted">
                {t("lastRotation")}
              </div>
              <div className="space-y-0.5">
                <div>
                  {t("previousVersion")}:{" "}
                  <code className="font-mono">{lastResult.previous_version}</code>
                </div>
                <div>
                  {t("newVersion")}:{" "}
                  <code className="font-mono">{lastResult.new_version}</code>
                </div>
                <div>
                  {t("rewrappedCount")}: {lastResult.rewrapped_count}
                  {lastResult.skipped_count > 0 &&
                    ` · ${t("skippedCount")}: ${lastResult.skipped_count}`}
                  {" · "}
                  {lastResult.duration_ms} ms
                </div>
              </div>
            </div>
          )}
        </CardContent>
      </Card>
    </div>
  );
}

function Row({
  label,
  children,
}: {
  label: string;
  children: React.ReactNode;
}) {
  return (
    <div className="flex items-center justify-between gap-3">
      <span className="text-[12px] sh-muted">{label}</span>
      <div>{children}</div>
    </div>
  );
}
