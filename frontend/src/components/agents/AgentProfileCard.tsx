"use client";

import { useState } from "react";
import { IconLoader2, IconRefresh, IconSparkles } from "@tabler/icons-react";
import { useLocale, useTranslations } from "next-intl";
import { toast } from "sonner";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import {
  type AgentProfileCommonError,
  type AgentProfileDomain,
  type AgentProfileErrorPattern,
  type AgentProfileHallucinationKind,
  type AgentProfileSkillCategory,
  type AgentProfileToolset,
  useAgentProfile,
  useRefreshAgentProfile,
} from "@/hooks/use-agent-profile";
import { relativeTime } from "@/lib/utils";

interface AgentProfileCardProps {
  agentId: string;
  isAdmin: boolean;
}

export function AgentProfileCard({ agentId, isAdmin }: AgentProfileCardProps) {
  const t = useTranslations("agentProfile");
  const locale = useLocale();
  const { data, isLoading } = useAgentProfile(agentId);
  const refresh = useRefreshAgentProfile();
  const [refreshing, setRefreshing] = useState(false);

  const onRefresh = async () => {
    setRefreshing(true);
    try {
      await refresh.mutateAsync({ agentId });
      toast.success(t("refreshSuccessToast"));
    } catch {
      toast.error(t("refreshFailedToast"));
    } finally {
      setRefreshing(false);
    }
  };

  if (isLoading) {
    return (
      <section className="rounded-md border sh-card p-5">
        <Skeleton className="mb-4 h-6 w-32" />
        <Skeleton className="mb-2 h-4 w-full" />
        <Skeleton className="mb-2 h-4 w-3/4" />
      </section>
    );
  }

  return (
    <section className="rounded-md border sh-card p-5 space-y-5">
      <header className="flex items-start justify-between gap-3">
        <div>
          <h3 className="flex items-center gap-2 text-base font-semibold">
            <IconSparkles className="size-4 text-[rgb(var(--color-primary))]" />
            {t("title")}
          </h3>
          {data ? (
            <p className="mt-1 text-[12px] sh-muted">
              {t("lastAggregatedLabel")}:{" "}
              {data.last_aggregated_at
                ? relativeTime(data.last_aggregated_at, locale)
                : t("noProfile")}
              {" · "}
              {t("aggregatedRunCount", { count: data.aggregated_run_count })}
            </p>
          ) : (
            <p className="mt-1 text-[12px] sh-muted">{t("noProfile")}</p>
          )}
        </div>
        {isAdmin ? (
          <Button
            variant="outline"
            size="sm"
            onClick={onRefresh}
            disabled={refreshing || refresh.isPending}
          >
            {refreshing ? (
              <IconLoader2 className="size-4 animate-spin" />
            ) : (
              <IconRefresh className="size-4" />
            )}
            {t("refreshButton")}
          </Button>
        ) : null}
      </header>

      {data ? (
        <div className="grid grid-cols-1 gap-5 md:grid-cols-2">
          <Strengths data={data.strengths_json} />
          <FailureModes data={data.failure_modes_json} />
        </div>
      ) : null}
    </section>
  );
}

function Strengths({
  data,
}: {
  data: import("@/hooks/use-agent-profile").AgentProfileStrengths;
}) {
  const t = useTranslations("agentProfile");
  const toolsets = data.toolsets ?? [];
  const skillCategories = data.skill_categories ?? [];
  const domains = data.domains ?? [];

  return (
    <div className="rounded-md border p-4 space-y-4">
      <h4 className="text-sm font-semibold">{t("strengthsLabel")}</h4>
      <BucketGroup
        label={t("toolsetsLabel")}
        items={toolsets.map((toolset: AgentProfileToolset) => ({
          primary: toolset.name,
          secondary: t("useCount", { count: toolset.use_count }),
          tertiary:
            toolset.effectiveness_avg !== null
              ? toolset.effectiveness_avg.toFixed(2)
              : "—",
        }))}
      />
      <BucketGroup
        label={t("skillCategoriesLabel")}
        items={skillCategories.map((cat: AgentProfileSkillCategory) => ({
          primary: cat.category,
          secondary: t("useCount", { count: cat.use_count }),
        }))}
      />
      <BucketGroup
        label={t("domainsLabel")}
        items={domains.map((d: AgentProfileDomain) => ({
          primary: d.domain,
          secondary: t("useCount", { count: d.use_count }),
          tertiary: d.judge_avg !== null ? d.judge_avg.toFixed(2) : "—",
        }))}
      />
    </div>
  );
}

function FailureModes({
  data,
}: {
  data: import("@/hooks/use-agent-profile").AgentProfileFailureModes;
}) {
  const t = useTranslations("agentProfile");
  const hallucinationKinds = data.hallucination_kinds ?? [];
  const commonErrors = data.common_errors ?? [];
  const errorPatterns = data.error_patterns ?? [];

  return (
    <div className="rounded-md border p-4 space-y-4">
      <h4 className="text-sm font-semibold">{t("failureModesLabel")}</h4>
      <BucketGroup
        label={t("hallucinationKindsLabel")}
        items={hallucinationKinds.map((row: AgentProfileHallucinationKind) => ({
          primary: row.kind,
          secondary: t("count", { count: row.count }),
        }))}
      />
      <BucketGroup
        label={t("commonErrorsLabel")}
        items={commonErrors.map((row: AgentProfileCommonError) => ({
          primary: row.error_kind,
          secondary: t("count", { count: row.count }),
        }))}
      />
      <BucketGroup
        label={t("errorPatternsLabel")}
        items={errorPatterns.map((row: AgentProfileErrorPattern) => ({
          primary: row.pattern_summary,
          secondary: t("count", {
            count: row.frequency ?? row.count ?? 0,
          }),
        }))}
        wide
      />
    </div>
  );
}

interface BucketRow {
  primary: string;
  secondary?: string;
  tertiary?: string;
}

function BucketGroup({
  label,
  items,
  wide,
}: {
  label: string;
  items: BucketRow[];
  wide?: boolean;
}) {
  const t = useTranslations("agentProfile");
  if (items.length === 0) {
    return (
      <div>
        <h5 className="mb-2 text-[12px] font-medium uppercase tracking-wide sh-muted">
          {label}
        </h5>
        <p className="text-[12px] sh-muted">{t("emptyBucket")}</p>
      </div>
    );
  }
  return (
    <div>
      <h5 className="mb-2 text-[12px] font-medium uppercase tracking-wide sh-muted">
        {label}
      </h5>
      <ul className="space-y-1.5">
        {items.map((row, idx) => (
          <li
            key={`${row.primary}-${idx}`}
            className="flex items-start gap-2 text-[13px]"
          >
            <span
              className={`min-w-0 ${wide ? "flex-1 break-words" : "truncate"}`}
              title={row.primary}
            >
              {row.primary}
            </span>
            {row.secondary ? (
              <Badge variant="outline" className="shrink-0">
                {row.secondary}
              </Badge>
            ) : null}
            {row.tertiary ? (
              <span className="shrink-0 font-mono text-[11px] sh-muted">
                {row.tertiary}
              </span>
            ) : null}
          </li>
        ))}
      </ul>
    </div>
  );
}
