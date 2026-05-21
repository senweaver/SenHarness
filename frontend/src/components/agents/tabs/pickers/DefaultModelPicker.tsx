"use client";

import { useMemo } from "react";
import { useTranslations } from "next-intl";
import { toast } from "sonner";

import { InlinePicker, type InlinePickerOption } from "./InlinePicker";
import { useAgentModels } from "@/hooks/use-agent-models";
import { useUpdateAgent } from "@/hooks/use-agent-mutations";
import type { AgentRead } from "@/types/api";

interface DefaultModelPickerProps {
  agent: AgentRead;
}

export function DefaultModelPicker({ agent }: DefaultModelPickerProps) {
  const t = useTranslations("settings.agents.detail.pickers");
  const modelsQ = useAgentModels(agent.id);
  const update = useUpdateAgent(agent.id);

  const options = useMemo<InlinePickerOption<string>[]>(() => {
    const rows = modelsQ.data?.options ?? [];
    return rows.map((r) => ({
      value: r.id,
      label: `${r.provider}/${r.name}`,
      description: r.description || undefined,
    }));
  }, [modelsQ.data]);

  const commit = async (next: string) => {
    try {
      await update.mutateAsync({ default_model: next });
    } catch (err) {
      toast.error(t("saveFailed", { error: (err as Error).message }));
    }
  };

  return (
    <InlinePicker
      label={t("defaultModel")}
      value={agent.default_model ?? null}
      options={options}
      onChange={commit}
      placeholder={t("defaultModelEmpty")}
      pending={update.isPending}
      disabled={options.length === 0}
    />
  );
}
