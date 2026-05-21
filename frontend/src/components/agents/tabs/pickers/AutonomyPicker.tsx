"use client";

import { useTranslations } from "next-intl";
import { toast } from "sonner";

import { InlinePicker } from "./InlinePicker";
import { useUpdateAgent } from "@/hooks/use-agent-mutations";
import type { AgentRead } from "@/types/api";

interface AutonomyPickerProps {
  agent: AgentRead;
}

export function AutonomyPicker({ agent }: AutonomyPickerProps) {
  const t = useTranslations("settings.agents.detail.pickers");
  const update = useUpdateAgent(agent.id);

  const commit = async (next: "l1" | "l2" | "l3") => {
    try {
      await update.mutateAsync({ autonomy_level: next });
    } catch (err) {
      toast.error(t("saveFailed", { error: (err as Error).message }));
    }
  };

  return (
    <InlinePicker<"l1" | "l2" | "l3">
      label={t("autonomy")}
      value={agent.autonomy_level}
      options={[
        { value: "l1", label: t("autonomyL1"), description: t("autonomyL1Desc") },
        { value: "l2", label: t("autonomyL2"), description: t("autonomyL2Desc") },
        { value: "l3", label: t("autonomyL3"), description: t("autonomyL3Desc") },
      ]}
      onChange={commit}
      pending={update.isPending}
    />
  );
}
