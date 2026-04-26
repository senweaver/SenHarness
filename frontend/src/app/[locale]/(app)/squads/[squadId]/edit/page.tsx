"use client";

import { use } from "react";
import { useTranslations } from "next-intl";
import { SquadForm } from "@/components/squads/SquadForm";
import { PageHeader } from "@/components/ui/page-header";
import { Skeleton } from "@/components/ui/skeleton";
import { useSquad } from "@/hooks/use-squads";

export default function EditSquadPage({
  params,
}: {
  params: Promise<{ squadId: string }>;
}) {
  const { squadId } = use(params);
  const t = useTranslations("settings.squads");
  const { data, isLoading } = useSquad(squadId);

  return (
    <div className="p-6">
      <PageHeader title={t("edit")} description={data?.name} />
      {isLoading && <Skeleton className="h-[420px]" />}
      {data && <SquadForm mode="edit" initial={data} />}
    </div>
  );
}
