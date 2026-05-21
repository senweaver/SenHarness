"use client";

import { useQuery } from "@tanstack/react-query";

import { api } from "@/lib/api";
import { useAuthStore } from "@/stores/auth-store";
import { useWorkspaceStore } from "@/stores/workspace-store";

/**
 * Frontmatter card for a skill that's enabled on an agent. Returned from
 * ``GET /api/v1/agents/{id}/skills`` — used by the chat composer's "/" palette
 * to render selectable skill rows. The full SKILL.md body is fetched lazily
 * via ``GET /api/v1/skills/{source}/{slug}`` if the user clicks "preview".
 */
export interface AgentSkillCard {
  slug: string;
  name: string;
  description: string;
  source: "bundled" | "workspace";
}

export function useAgentSkills(agentId: string | null | undefined) {
  const token = useAuthStore((s) => s.accessToken);
  const ws = useWorkspaceStore((s) => s.activeWorkspaceId);
  return useQuery<AgentSkillCard[]>({
    queryKey: ["agents", agentId, "skills", ws],
    queryFn: () => api.get<AgentSkillCard[]>(`/api/v1/agents/${agentId}/skills`),
    enabled: Boolean(token && ws && agentId),
    // Skills very rarely change mid-session; prevent the chat surface from
    // re-fetching every keystroke when the slash menu re-opens.
    staleTime: 5 * 60_000,
  });
}
