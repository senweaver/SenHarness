"use client";

import { useTranslations } from "next-intl";
import { toast } from "sonner";

import { InlinePicker } from "./InlinePicker";
import { useUpdateAgent } from "@/hooks/use-agent-mutations";
import type { AgentRead } from "@/types/api";

interface VisibilityPickerProps {
  agent: AgentRead;
}

export function VisibilityPicker({ agent }: VisibilityPickerProps) {
  const t = useTranslations("settings.agents.detail.pickers");
  const update = useUpdateAgent(agent.id);

  const commit = async (next: "private" | "workspace" | "public") => {
    try {
      await update.mutateAsync({ visibility: next });
    } catch (err) {
      toast.error(t("saveFailed", { error: (err as Error).message }));
    }
  };

  return (
    <InlinePicker<"private" | "workspace" | "public">
      label={t("visibility")}
      value={agent.visibility}
      options={[
        { value: "private", label: t("visibilityPrivate") },
        { value: "workspace", label: t("visibilityWorkspace") },
        { value: "public", label: t("visibilityPublic") },
      ]}
      onChange={commit}
      pending={update.isPending}
    />
  );
}
