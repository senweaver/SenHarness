"use client";

import { use } from "react";
import { useTranslations } from "next-intl";
import { AgentForm } from "@/components/agents/AgentForm";
import { PageHeader } from "@/components/ui/page-header";
import { Skeleton } from "@/components/ui/skeleton";
import { useAgent } from "@/hooks/use-agent-mutations";

export default function EditAgentPage({ params }: { params: Promise<{ agentId: string }> }) {
  const { agentId } = use(params);
  const t = useTranslations("settings.agents");
  const { data, isLoading } = useAgent(agentId);

  return (
    <div className="p-6">
      <PageHeader title={t("edit")} description={data?.name} />
      {isLoading && <Skeleton className="h-[420px]" />}
      {data && <AgentForm mode="edit" initial={data} />}
    </div>
  );
}
