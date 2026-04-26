"use client";

import { useTranslations } from "next-intl";
import { FlowForm } from "@/components/flows/FlowForm";
import { PageHeader } from "@/components/ui/page-header";

export default function NewFlowPage() {
  const t = useTranslations("flows");
  return (
    <div className="p-6">
      <PageHeader title={t("new")} description={t("description")} />
      <FlowForm mode="create" />
    </div>
  );
}
