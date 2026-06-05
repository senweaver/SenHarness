"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { useAgents } from "@/hooks/use-agents";
import { api } from "@/lib/api";
import { useAuthStore } from "@/stores/auth-store";
import { useWorkspaceStore } from "@/stores/workspace-store";

/**
 * One row in the chat composer's model dropdown for a given agent.
 *
 * ``id`` is the canonical ``provider:model`` selector — same shape the WS
 * transport forwards to the kernel as ``RunRequest.model_override``. The
 * frontend never tries to interpret it; the backend kernel parses it.
 */
export interface AgentModelOption {
  id: string;
  provider: string;
  provider_display_name: string;
  model: string;
  name: string;
  family: string;
  recommended: boolean;
  description: string;
  is_default: boolean;
  /** Catalog-resolved reasoning support — drives the composer's thinking
   *  mode guardrail. ``false`` when the model exposes no thinking phase. */
  reasoning_supported: boolean;
}

export interface AgentModelsResponse {
  provider: string | null;
  default_model: string | null;
  source: string | null;
  options: AgentModelOption[];
}

/** Fetch the models offered to the chat composer for ``agentId``.
 *
 * Read-only and rarely changes mid-session, so cache aggressively. The
 * cache key is per-(agent, workspace) so switching workspaces refetches
 * automatically. */
export function useAgentModels(agentId: string | null | undefined) {
  const token = useAuthStore((s) => s.accessToken);
  const ws = useWorkspaceStore((s) => s.activeWorkspaceId);
  return useQuery<AgentModelsResponse>({
    queryKey: ["agents", agentId, "models", ws],
    queryFn: async () => {
      const res = await api.get<AgentModelsResponse>(
        `/api/v1/agents/${agentId}/models`,
      );
      return res;
    },
    enabled: Boolean(token && ws && agentId),
    staleTime: 5 * 60_000,
  });
}

/**
 * Workspace-scoped model catalog for the "new agent" surface, where we
 * don't yet have an agent id to query. The backend only exposes the
 * model list via `/agents/{id}/models`; the `options` payload is
 * workspace-wide so we reuse any existing agent's id as a probe.
 * Returns an empty list when the workspace has no agents yet.
 */
export function useWorkspaceModelOptions() {
  const token = useAuthStore((s) => s.accessToken);
  const ws = useWorkspaceStore((s) => s.activeWorkspaceId);
  const { data: agents } = useAgents();
  const probeId = agents?.[0]?.id ?? null;
  return useQuery<AgentModelOption[]>({
    queryKey: ["workspace-models", ws, probeId],
    queryFn: async () => {
      if (!probeId) return [];
      const resp = await api.get<AgentModelsResponse>(
        `/api/v1/agents/${probeId}/models`,
      );
      return resp.options;
    },
    enabled: Boolean(token && ws && probeId),
    staleTime: 5 * 60_000,
  });
}

// ─── User preferences (per-agent saved model picks) ───────────

/** Map of ``agent_id`` (or ``"default"``) → ``"provider:model"`` selector. */
export interface ChatModelPrefs {
  prefs: Record<string, string>;
}

export function useChatModelPrefs() {
  const token = useAuthStore((s) => s.accessToken);
  return useQuery<ChatModelPrefs>({
    queryKey: ["me", "preferences", "models"],
    queryFn: () => api.get<ChatModelPrefs>(`/api/v1/me/preferences/models`),
    enabled: Boolean(token),
    staleTime: 5 * 60_000,
  });
}

interface SetChatModelPrefVars {
  /** ``null`` writes the global default applied when no agent-specific entry is set. */
  agentId: string | null;
  /** ``null`` clears the saved entry for this agent. */
  model: string | null;
}

export function useSetChatModelPref() {
  const qc = useQueryClient();
  return useMutation<ChatModelPrefs, Error, SetChatModelPrefVars>({
    mutationFn: ({ agentId, model }) =>
      api.put<ChatModelPrefs>(`/api/v1/me/preferences/models`, {
        agent_id: agentId,
        model,
      }),
    onSuccess: (data) => {
      qc.setQueryData(["me", "preferences", "models"], data);
    },
  });
}
