"use client";

import { useTranslations } from "next-intl";
import { toast } from "sonner";

import { InlinePicker } from "./InlinePicker";
import { useUpdateAgent } from "@/hooks/use-agent-mutations";
import type { AgentRead } from "@/types/api";

type SandboxKind = "off" | "local" | "docker";

interface SandboxPickerProps {
  agent: AgentRead;
}

function readSandboxKind(agent: AgentRead): SandboxKind {
  const meta = (agent.metadata_json ?? {}) as { sandbox?: unknown };
  const raw = meta.sandbox;
  if (typeof raw === "string") {
    if (raw === "off" || raw === "local" || raw === "docker") return raw;
  }
  if (raw && typeof raw === "object") {
    const kind = (raw as { kind?: unknown }).kind;
    if (kind === "off" || kind === "local" || kind === "docker") return kind;
  }
  return "off";
}

export function SandboxPicker({ agent }: SandboxPickerProps) {
  const t = useTranslations("settings.agents.detail.pickers");
  const update = useUpdateAgent(agent.id);

  const value = readSandboxKind(agent);

  const commit = async (next: SandboxKind) => {
    const meta = (agent.metadata_json ?? {}) as Record<string, unknown>;
    const prev = meta.sandbox;
    let nextSandbox: unknown;
    if (prev && typeof prev === "object" && !Array.isArray(prev)) {
      nextSandbox = { ...(prev as Record<string, unknown>), kind: next };
    } else {
      nextSandbox = next;
    }
    try {
      await update.mutateAsync({
        metadata_json: { ...meta, sandbox: nextSandbox },
      });
    } catch (err) {
      toast.error(t("saveFailed", { error: (err as Error).message }));
    }
  };

  return (
    <InlinePicker<SandboxKind>
      label={t("sandbox")}
      value={value}
      options={[
        { value: "off", label: t("sandboxOff") },
        { value: "local", label: t("sandboxLocal") },
        { value: "docker", label: t("sandboxDocker") },
      ]}
      onChange={commit}
      pending={update.isPending}
    />
  );
}
