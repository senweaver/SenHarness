"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "@/lib/api";
import { useAuthStore } from "@/stores/auth-store";
import { useWorkspaceStore } from "@/stores/workspace-store";
import type { AgentPublicCard, AgentRead } from "@/types/api";

export function useDiscoverAgents(q = "", limit = 60) {
  const token = useAuthStore((s) => s.accessToken);
  const ws = useWorkspaceStore((s) => s.activeWorkspaceId);
  const qs = new URLSearchParams();
  if (q.trim()) qs.set("q", q.trim());
  if (limit !== 60) qs.set("limit", String(limit));
  const query = qs.toString();
  return useQuery<AgentPublicCard[]>({
    queryKey: ["marketplace", "agents", ws, query],
    queryFn: () =>
      api.get<AgentPublicCard[]>(
        `/api/v1/agents/discover${query ? "?" + query : ""}`,
      ),
    enabled: Boolean(token && ws),
    staleTime: 30 * 1000,
  });
}

export function useCloneAgent() {
  const qc = useQueryClient();
  return useMutation<
    AgentRead,
    unknown,
    { agent_id: string; name?: string | null }
  >({
    mutationFn: ({ agent_id, name }) =>
      api.post<AgentRead>(`/api/v1/agents/${agent_id}/clone`, {
        name: name ?? null,
      }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["agents"] });
    },
  });
}
