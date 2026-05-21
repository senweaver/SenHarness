"use client";

import { useMemo } from "react";
import { useTranslations } from "next-intl";
import { toast } from "sonner";

import { InlinePicker } from "./InlinePicker";
import { useBackendAdapters } from "@/hooks/use-backend-adapters";
import { useRegisteredRuntimes } from "@/hooks/use-runtimes";
import { useUpdateAgent } from "@/hooks/use-agent-mutations";
import type { AgentRead } from "@/types/api";

interface RuntimePickerProps {
  agent: AgentRead;
}

/**
 * RuntimeBackend chip. Picks ``backend_kind`` from the registered
 * runtimes; when the user lands on a remote kind (``requires_adapter``
 * true) the second chip below renders the per-workspace adapter list.
 */
export function RuntimePicker({ agent }: RuntimePickerProps) {
  const t = useTranslations("settings.agents.detail.pickers");
  const update = useUpdateAgent(agent.id);
  const runtimesQ = useRegisteredRuntimes();
  const adaptersQ = useBackendAdapters();

  const runtimeOptions = useMemo(
    () =>
      (runtimesQ.data ?? []).map((r) => ({
        value: r.kind,
        label: r.display_name || r.kind,
        description: r.description || undefined,
      })),
    [runtimesQ.data],
  );

  const activeRuntime = runtimesQ.data?.find(
    (r) => r.kind === agent.backend_kind,
  );

  const adapterOptions = useMemo(() => {
    const list = adaptersQ.data ?? [];
    return list
      .filter((a) => a.kind === agent.backend_kind || a.kind === "openclaw")
      .map((a) => ({
        value: a.id,
        label: a.name,
        description: a.endpoint ?? undefined,
      }));
  }, [adaptersQ.data, agent.backend_kind]);

  const commitKind = async (kind: string) => {
    try {
      await update.mutateAsync({
        backend_kind: kind as "native" | "openclaw",
        backend_adapter_id:
          kind === agent.backend_kind ? agent.backend_adapter_id : null,
      });
    } catch (err) {
      toast.error(t("saveFailed", { error: (err as Error).message }));
    }
  };

  const commitAdapter = async (id: string) => {
    try {
      await update.mutateAsync({ backend_adapter_id: id });
    } catch (err) {
      toast.error(t("saveFailed", { error: (err as Error).message }));
    }
  };

  return (
    <div className="flex flex-col items-end gap-1">
      <InlinePicker
        label={t("backend")}
        value={agent.backend_kind}
        options={runtimeOptions}
        onChange={commitKind}
        pending={update.isPending}
      />
      {activeRuntime?.requires_adapter ? (
        <InlinePicker
          label={t("adapter")}
          value={agent.backend_adapter_id ?? null}
          options={adapterOptions}
          onChange={commitAdapter}
          placeholder={t("adapterEmpty")}
          pending={update.isPending}
        />
      ) : null}
    </div>
  );
}
