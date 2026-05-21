"use client";

import { useTranslations } from "next-intl";
import { IconCircleCheckFilled, IconAlertTriangle } from "@tabler/icons-react";

import { useAuxConfig } from "@/hooks/use-aux-config";
import { cn } from "@/lib/utils";

export function AuxiliaryModelsCard() {
  const t = useTranslations("judge");
  const { data, isLoading } = useAuxConfig();

  if (isLoading || !data) {
    return null;
  }

  const breakerOpen = data.judge_breaker.open;
  return (
    <section className="border-t bg-card/30 px-6 py-4 text-sm">
      <header className="flex items-center justify-between">
        <h3 className="font-medium">{t("auxiliarySectionTitle")}</h3>
        <span className="text-xs text-muted-foreground">
          {t("auxiliarySectionHelp")}
        </span>
      </header>

      <dl className="mt-3 grid grid-cols-2 gap-x-6 gap-y-2 text-xs sm:grid-cols-3">
        <Row label={t("auxModelDefault")} value={data.aux_model_default} />
        <Row label={t("auxModelJudge")} value={data.aux_model_judge} />
        <Row
          label={t("auxModelGoalAlignment")}
          value={data.aux_model_goal_alignment}
        />
        <Row
          label={t("rateBudget")}
          value={t("rateBudgetUsage", {
            used: data.judge_rate.used,
            limit: data.judge_rate.limit,
          })}
        />
        <div className="flex items-center gap-2">
          {breakerOpen ? (
            <IconAlertTriangle
              className={cn("size-4", "text-rose-600")}
              aria-hidden
            />
          ) : (
            <IconCircleCheckFilled
              className={cn("size-4", "text-emerald-600")}
              aria-hidden
            />
          )}
          <span
            className={cn(
              "text-xs",
              breakerOpen ? "text-rose-600" : "text-emerald-600",
            )}
          >
            {breakerOpen ? t("breakerDegraded") : t("breakerHealthy")}
          </span>
        </div>
      </dl>
      <p className="mt-3 text-xs text-muted-foreground">
        {t("moveToAdminLink")}
      </p>
    </section>
  );
}

function Row({ label, value }: { label: string; value: string | null }) {
  return (
    <div className="min-w-0">
      <dt className="text-muted-foreground">{label}</dt>
      <dd className="truncate font-mono text-[11px]">{value ?? "—"}</dd>
    </div>
  );
}
