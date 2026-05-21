"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "@/lib/api";
import { useAuthStore } from "@/stores/auth-store";
import { useWorkspaceStore } from "@/stores/workspace-store";

export type MemoryScope = "user" | "assistant" | "workspace";
export type MemoryKind = "kv" | "episodic" | "semantic";

export interface MemoryRead {
  id: string;
  workspace_id: string;
  scope: MemoryScope;
  scope_id: string | null;
  kind: MemoryKind;
  key: string | null;
  content: string;
  value_json: Record<string, unknown>;
  confidence: number;
  ttl_at: string | null;
  embedding_model: string | null;
  source_session_id: string | null;
  author_identity_id: string | null;
  created_at: string;
  updated_at: string;
}

export interface MemoryCreate {
  scope?: MemoryScope;
  scope_id?: string | null;
  kind?: MemoryKind;
  key?: string | null;
  content: string;
  confidence?: number;
  ttl_seconds?: number | null;
}

export type MemoryUpdate = {
  content?: string;
  confidence?: number;
};

export function useMemories(params: {
  scope?: MemoryScope | null;
  kind?: MemoryKind | null;
  q?: string | null;
}) {
  const tok = useAuthStore((s) => s.accessToken);
  const ws = useWorkspaceStore((s) => s.activeWorkspaceId);
  const qs = new URLSearchParams();
  if (params.scope) qs.set("scope", params.scope);
  if (params.kind) qs.set("kind", params.kind);
  if (params.q) qs.set("q", params.q);
  return useQuery<MemoryRead[]>({
    queryKey: ["memories", ws, params.scope, params.kind, params.q ?? ""],
    queryFn: () => api.get<MemoryRead[]>(`/api/v1/memory?${qs.toString()}`),
    enabled: Boolean(tok && ws),
  });
}

export interface MemoryStats {
  by_scope: Record<string, number>;
  by_kind: Record<string, number>;
  total: number;
}

export function useMemoryStats() {
  const tok = useAuthStore((s) => s.accessToken);
  const ws = useWorkspaceStore((s) => s.activeWorkspaceId);
  return useQuery<MemoryStats>({
    queryKey: ["memories", "stats", ws],
    queryFn: () => api.get<MemoryStats>("/api/v1/memory/stats"),
    enabled: Boolean(tok && ws),
  });
}

export interface RecallHit {
  memory: MemoryRead;
  score: number;
}

export function useRecallMemory() {
  return useMutation<
    RecallHit[],
    unknown,
    { query: string; limit?: number; min_score?: number }
  >({
    mutationFn: (body) => api.post<RecallHit[]>("/api/v1/memory/recall", body),
  });
}

export function useCreateMemory() {
  const qc = useQueryClient();
  return useMutation<MemoryRead, unknown, MemoryCreate>({
    mutationFn: (input) => api.post<MemoryRead>("/api/v1/memory", input),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["memories"] }),
  });
}

export function useUpdateMemory(id: string) {
  const qc = useQueryClient();
  return useMutation<MemoryRead, unknown, MemoryUpdate>({
    mutationFn: (input) => api.patch<MemoryRead>(`/api/v1/memory/${id}`, input),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["memories"] }),
  });
}

export function useDeleteMemory() {
  const qc = useQueryClient();
  return useMutation<void, unknown, string>({
    mutationFn: (id) => api.delete(`/api/v1/memory/${id}`),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["memories"] }),
  });
}
