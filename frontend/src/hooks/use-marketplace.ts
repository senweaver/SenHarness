"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "@/lib/api";
import { useAuthStore } from "@/stores/auth-store";
import { useWorkspaceStore } from "@/stores/workspace-store";
import type {
  AgentCategory,
  AgentPublicCard,
  AgentRead,
} from "@/types/api";

export interface DiscoverFilters {
  q?: string;
  category?: string | null;
  tag?: string | null;
  templateOnly?: boolean;
  limit?: number;
}

export function useDiscoverAgents(filters: DiscoverFilters = {}) {
  const token = useAuthStore((s) => s.accessToken);
  const ws = useWorkspaceStore((s) => s.activeWorkspaceId);

  const { q = "", category = null, tag = null, templateOnly = false, limit = 60 } = filters;

  const qs = new URLSearchParams();
  if (q.trim()) qs.set("q", q.trim());
  if (category) qs.set("category", category);
  if (tag) qs.set("tag", tag);
  if (templateOnly) qs.set("template_only", "true");
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

export function useDiscoverCategories(
  options: { templateOnly?: boolean } = {},
) {
  const token = useAuthStore((s) => s.accessToken);
  const ws = useWorkspaceStore((s) => s.activeWorkspaceId);
  const templateOnly = options.templateOnly ?? false;
  const query = templateOnly ? "?template_only=true" : "";
  return useQuery<AgentCategory[]>({
    queryKey: ["marketplace", "categories", ws, templateOnly],
    queryFn: () =>
      api.get<AgentCategory[]>(`/api/v1/agents/discover/categories${query}`),
    enabled: Boolean(token && ws),
    staleTime: 5 * 60 * 1000,
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
