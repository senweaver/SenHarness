"use client";

import { useQuery } from "@tanstack/react-query";
import { api } from "@/lib/api";
import { useAuthStore } from "@/stores/auth-store";
import { useWorkspaceStore } from "@/stores/workspace-store";
import type { AgentRead, AgentRecent } from "@/types/api";

export function useRecentAgents(limit = 5) {
  const token = useAuthStore((s) => s.accessToken);
  const ws = useWorkspaceStore((s) => s.activeWorkspaceId);
  return useQuery<AgentRecent[]>({
    queryKey: ["agents", "recent", ws, limit],
    queryFn: () => api.get<AgentRecent[]>(`/api/v1/agents/recent?limit=${limit}`),
    enabled: Boolean(token && ws),
  });
}

export function useAgents() {
  const token = useAuthStore((s) => s.accessToken);
  const ws = useWorkspaceStore((s) => s.activeWorkspaceId);
  return useQuery<AgentRead[]>({
    queryKey: ["agents", "list", ws],
    queryFn: () => api.get<AgentRead[]>("/api/v1/agents"),
    enabled: Boolean(token && ws),
  });
}
