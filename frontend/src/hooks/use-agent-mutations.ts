"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "@/lib/api";
import type { AgentRead } from "@/types/api";

export interface AgentCreateInput {
  name: string;
  description?: string | null;
  persona_md?: string | null;
  avatar_url?: string | null;
  backend_kind?: "native" | "openclaw";
  backend_adapter_id?: string | null;
  autonomy_level?: "l1" | "l2" | "l3";
  visibility?: "private" | "workspace" | "public";
  metadata_json?: Record<string, unknown>;
}

export type AgentUpdateInput = Partial<AgentCreateInput>;

export function useAgent(agentId: string | null | undefined) {
  return useQuery<AgentRead>({
    queryKey: ["agent", agentId],
    queryFn: () => api.get<AgentRead>(`/api/v1/agents/${agentId}`),
    enabled: Boolean(agentId),
  });
}

export function useCreateAgent() {
  const qc = useQueryClient();
  return useMutation<AgentRead, unknown, AgentCreateInput>({
    mutationFn: (input) => api.post<AgentRead>("/api/v1/agents", input),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["agents"] });
    },
  });
}

export function useUpdateAgent(agentId: string) {
  const qc = useQueryClient();
  return useMutation<AgentRead, unknown, AgentUpdateInput>({
    mutationFn: (input) => api.patch<AgentRead>(`/api/v1/agents/${agentId}`, input),
    onSuccess: (updated) => {
      qc.setQueryData(["agent", agentId], updated);
      qc.invalidateQueries({ queryKey: ["agents"] });
    },
  });
}

export function useDeleteAgent() {
  const qc = useQueryClient();
  return useMutation<void, unknown, string>({
    mutationFn: (id) => api.delete(`/api/v1/agents/${id}`),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["agents"] });
    },
  });
}

export interface StarAgentResult {
  agent_id: string;
  starred: boolean;
  pinned: boolean;
}

export function useIsAgentStarred(agentId: string | null | undefined) {
  return useQuery<AgentRead[]>({
    queryKey: ["agents", "starred"],
    queryFn: () => api.get<AgentRead[]>("/api/v1/agents/starred"),
    enabled: Boolean(agentId),
    select: (items) => items,
  });
}

export function useToggleStar(agentId: string) {
  const qc = useQueryClient();
  return useMutation<
    StarAgentResult | null,
    unknown,
    { starred: boolean; pinned?: boolean }
  >({
    mutationFn: async ({ starred, pinned }) => {
      if (starred) {
        return api.post<StarAgentResult>(
          `/api/v1/agents/${agentId}/star${pinned ? "?pinned=true" : ""}`,
          {},
        );
      }
      await api.delete(`/api/v1/agents/${agentId}/star`);
      return null;
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["agents"] });
    },
  });
}
