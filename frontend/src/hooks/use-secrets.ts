"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "@/lib/api";
import { useAuthStore } from "@/stores/auth-store";
import { useWorkspaceStore } from "@/stores/workspace-store";

export interface SecretRead {
  id: string;
  workspace_id: string;
  name: string;
  kind: string;
  required_approval: boolean;
  metadata_json: Record<string, unknown>;
  created_at: string;
  updated_at: string;
}

export interface SecretCreate {
  name: string;
  value: string;
  kind?: string;
  metadata_json?: Record<string, unknown>;
  required_approval?: boolean;
}

export function useSecrets() {
  const tok = useAuthStore((s) => s.accessToken);
  const ws = useWorkspaceStore((s) => s.activeWorkspaceId);
  return useQuery<SecretRead[]>({
    queryKey: ["secrets", ws],
    queryFn: () => api.get<SecretRead[]>("/api/v1/secrets"),
    enabled: Boolean(tok && ws),
  });
}

export function useCreateSecret() {
  const qc = useQueryClient();
  const ws = useWorkspaceStore((s) => s.activeWorkspaceId);
  return useMutation<SecretRead, unknown, SecretCreate>({
    mutationFn: (body) => api.post<SecretRead>("/api/v1/secrets", body),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["secrets", ws] }),
  });
}

export function useUpdateSecret(secretId: string) {
  const qc = useQueryClient();
  const ws = useWorkspaceStore((s) => s.activeWorkspaceId);
  return useMutation<SecretRead, unknown, Partial<SecretCreate>>({
    mutationFn: (body) => api.patch<SecretRead>(`/api/v1/secrets/${secretId}`, body),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["secrets", ws] }),
  });
}

export function useDeleteSecret() {
  const qc = useQueryClient();
  const ws = useWorkspaceStore((s) => s.activeWorkspaceId);
  return useMutation<void, unknown, string>({
    mutationFn: (id) => api.delete(`/api/v1/secrets/${id}`),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["secrets", ws] }),
  });
}

export async function revealSecret(id: string): Promise<string> {
  const res = await api.post<{ value: string }>(`/api/v1/secrets/${id}/reveal`, {});
  return res.value;
}
