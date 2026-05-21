"use client";

import { useTranslations } from "next-intl";
import { SquadForm } from "@/components/squads/SquadForm";
import { PageHeader } from "@/components/ui/page-header";

export default function NewSquadPage() {
  const t = useTranslations("settings.squads");
  return (
    <div className="p-6">
      <PageHeader title={t("new")} description={t("description")} />
      <SquadForm mode="create" />
    </div>
  );
}
