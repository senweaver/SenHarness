"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "@/lib/api";
import { useAuthStore } from "@/stores/auth-store";
import { useWorkspaceStore } from "@/stores/workspace-store";

export interface SearchProviderRead {
  id: string;
  workspace_id: string;
  kind: string;
  name: string;
  base_url: string | null;
  enabled: boolean;
  priority: number;
  metadata_json: Record<string, unknown>;
  has_key: boolean;
  created_at: string;
  updated_at: string;
}

export interface SearchProviderCreate {
  kind: string;
  name: string;
  base_url?: string | null;
  enabled?: boolean;
  priority?: number;
  api_key?: string | null;
}

export type SearchProviderUpdate = Partial<SearchProviderCreate>;

export interface SearchProviderCatalogEntry {
  kind: string;
  display_name: string;
  display_name_zh: string;
  description: string;
  description_zh: string;
  default_base_url: string | null;
  needs_key: boolean;
}

export function useSearchProviderCatalog() {
  const tok = useAuthStore((s) => s.accessToken);
  return useQuery<SearchProviderCatalogEntry[]>({
    queryKey: ["search-provider-catalog"],
    queryFn: () =>
      api.get<SearchProviderCatalogEntry[]>(
        "/api/v1/search-providers/catalog",
      ),
    enabled: Boolean(tok),
    staleTime: 5 * 60 * 1000,
  });
}

export function useSearchProviders() {
  const tok = useAuthStore((s) => s.accessToken);
  const ws = useWorkspaceStore((s) => s.activeWorkspaceId);
  return useQuery<SearchProviderRead[]>({
    queryKey: ["search-providers", ws],
    queryFn: () => api.get<SearchProviderRead[]>("/api/v1/search-providers"),
    enabled: Boolean(tok && ws),
  });
}

export function useCreateSearchProvider() {
  const qc = useQueryClient();
  return useMutation<SearchProviderRead, unknown, SearchProviderCreate>({
    mutationFn: (input) =>
      api.post<SearchProviderRead>("/api/v1/search-providers", input),
    onSuccess: () =>
      qc.invalidateQueries({ queryKey: ["search-providers"] }),
  });
}

export function useUpdateSearchProvider(id: string) {
  const qc = useQueryClient();
  return useMutation<SearchProviderRead, unknown, SearchProviderUpdate>({
    mutationFn: (input) =>
      api.patch<SearchProviderRead>(`/api/v1/search-providers/${id}`, input),
    onSuccess: () =>
      qc.invalidateQueries({ queryKey: ["search-providers"] }),
  });
}

export function useDeleteSearchProvider() {
  const qc = useQueryClient();
  return useMutation<void, unknown, string>({
    mutationFn: (id) => api.delete(`/api/v1/search-providers/${id}`),
    onSuccess: () =>
      qc.invalidateQueries({ queryKey: ["search-providers"] }),
  });
}
