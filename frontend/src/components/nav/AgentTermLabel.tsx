"use client";

import { useTranslations } from "next-intl";
import { useWorkspaceStore } from "@/stores/workspace-store";

/**
 * Renders the workspace-configured "agent term" (智能体 / 数字员工 / AI 伙伴 / 小秘书 / 助理)
 * falling back to the locale-specific default translation when unset.
 *
 * We key off `branding.agent_term` which the backend stores as a canonical slug:
 *   "default" | "digital_employee" | "agent" | "partner" | "secretary"
 *
 * The fallback for unset workspaces is the `agent` slug (智能体 / Agent),
 * matching DEFAULT_BRANDING in the backend.
 *
 * (Free-form string fallback is supported too.)
 */
const VALID_TERM_SLUGS = ["default", "digital_employee", "agent", "partner", "secretary"] as const;

export function useAgentTerm(): string {
  const t = useTranslations("agentTerm");
  const term = useWorkspaceStore(
    (s) => s.workspaces.find((w) => w.id === s.activeWorkspaceId)?.branding?.agent_term,
  );
  if (!term) return t("agent");
  if ((VALID_TERM_SLUGS as readonly string[]).includes(term)) {
    return t(term as typeof VALID_TERM_SLUGS[number]);
  }
  return term;
}

export function AgentTermLabel({ capitalize = false }: { capitalize?: boolean }) {
  const term = useAgentTerm();
  return <span>{capitalize ? term : term}</span>;
}
