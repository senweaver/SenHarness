"use client";

import { useEffect, useState } from "react";
import { useRouter } from "@/lib/navigation";
import { useSearchParams } from "next/navigation";
import { IconPlus } from "@tabler/icons-react";
import { useTranslations } from "next-intl";

import { Button } from "@/components/ui/button";
import { PageHeader } from "@/components/ui/page-header";
import { useAgentTerm } from "@/components/nav/AgentTermLabel";
import { BlankAgentDialog } from "@/components/agents/BlankAgentDialog";
import { NewAgentDialog } from "@/components/agents/NewAgentDialog";
import { AgentsListBody } from "@/components/agents/AgentsListBody";

export default function AgentsPage() {
  const t = useTranslations();
  const tAgents = useTranslations("settings.agents");
  const term = useAgentTerm();
  const router = useRouter();
  const searchParams = useSearchParams();

  const [dialogOpen, setDialogOpen] = useState(false);
  const [blankOpen, setBlankOpen] = useState(false);

  useEffect(() => {
    if (searchParams.get("new") === "1") {
      setDialogOpen(true);
    }
  }, [searchParams]);

  const onDialogChange = (open: boolean) => {
    setDialogOpen(open);
    if (!open && searchParams.get("new") === "1") {
      const next = new URLSearchParams(searchParams);
      next.delete("new");
      const qs = next.toString();
      router.replace(`/agents${qs ? `?${qs}` : ""}`);
    }
  };

  return (
    <div className="p-6">
      <PageHeader
        title={term}
        description={tAgents("description")}
        actions={
          <Button size="sm" onClick={() => setDialogOpen(true)}>
            <IconPlus className="size-4" />
            {tAgents("new")}
          </Button>
        }
      />

      <AgentsListBody onNew={() => setDialogOpen(true)} />

      <NewAgentDialog
        open={dialogOpen}
        onOpenChange={onDialogChange}
        onPickBlank={() => {
          onDialogChange(false);
          setBlankOpen(true);
        }}
      />

      <BlankAgentDialog open={blankOpen} onOpenChange={setBlankOpen} />

      <span className="sr-only">{t("common.loading")}</span>
    </div>
  );
}
