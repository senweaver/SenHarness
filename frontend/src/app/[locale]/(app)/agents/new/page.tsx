"use client";

import { useTranslations } from "next-intl";
import { AgentForm } from "@/components/agents/AgentForm";
import { PageHeader } from "@/components/ui/page-header";

export default function NewAgentPage() {
  const t = useTranslations("settings.agents");
  return (
    <div className="p-6">
      <PageHeader title={t("new")} description={t("description")} />
      <AgentForm mode="create" />
    </div>
  );
}
