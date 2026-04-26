"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "@/lib/api";
import { useAuthStore } from "@/stores/auth-store";
import { useWorkspaceStore } from "@/stores/workspace-store";

export type ProviderKind =
  | "openai"
  | "anthropic"
  | "google"
  | "openrouter"
  | "azure_openai"
  | "deepseek"
  | "moonshot"
  | "groq"
  | "ollama"
  | "vllm"
  | "sglang"
  | "custom";

export interface ProviderRead {
  id: string;
  workspace_id: string;
  kind: ProviderKind;
  name: string;
  base_url: string | null;
  default_model: string | null;
  enabled: boolean;
  metadata_json: Record<string, unknown>;
  has_key: boolean;
  created_at: string;
  updated_at: string;
}

export interface ProviderCreate {
  kind: ProviderKind;
  name: string;
  base_url?: string | null;
  default_model?: string | null;
  api_key?: string | null;
  enabled?: boolean;
}

export type ProviderUpdate = Partial<ProviderCreate>;

export function useProviders() {
  const tok = useAuthStore((s) => s.accessToken);
  const ws = useWorkspaceStore((s) => s.activeWorkspaceId);
  return useQuery<ProviderRead[]>({
    queryKey: ["providers", ws],
    queryFn: () => api.get<ProviderRead[]>("/api/v1/providers"),
    enabled: Boolean(tok && ws),
  });
}

export function useCreateProvider() {
  const qc = useQueryClient();
  return useMutation<ProviderRead, unknown, ProviderCreate>({
    mutationFn: (input) => api.post<ProviderRead>("/api/v1/providers", input),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["providers"] }),
  });
}

export function useUpdateProvider(id: string) {
  const qc = useQueryClient();
  return useMutation<ProviderRead, unknown, ProviderUpdate>({
    mutationFn: (input) => api.patch<ProviderRead>(`/api/v1/providers/${id}`, input),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["providers"] }),
  });
}

export function useDeleteProvider() {
  const qc = useQueryClient();
  return useMutation<void, unknown, string>({
    mutationFn: (id) => api.delete(`/api/v1/providers/${id}`),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["providers"] }),
  });
}
