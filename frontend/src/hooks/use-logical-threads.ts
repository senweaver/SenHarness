"use client";

/**
 * Cross-platform logical thread hooks (M3.6).
 *
 * Backend: `backend/app/api/v1/threads.py` —
 * `/threads`, `/threads/{id}`, `/threads/{id}/sessions/active`,
 * `/threads/{id}/label`, `/threads/pair/initiate`,
 * `/threads/pair/consume`, `/threads/{id}/bindings`,
 * `/threads/{id}/bindings/{binding_id}` (8 routes).
 *
 * The thread badge in the chat list uses `useLogicalThreads`; the
 * `/settings/cross-platform` page composes the rest.
 */

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api } from "@/lib/api";
import { useAuthStore } from "@/stores/auth-store";
import { useWorkspaceStore } from "@/stores/workspace-store";

export interface LogicalThreadRead {
  id: string;
  workspace_id: string;
  identity_id: string;
  agent_id: string;
  primary_session_id: string;
  label: string | null;
  last_activity_at: string;
  created_at: string;
  updated_at: string;
}

export interface ThreadChannelBindingRead {
  id: string;
  thread_id: string;
  channel_id: string | null;
  channel_name: string | null;
  channel_kind: string | null;
  external_user_id: string | null;
  last_seen_at: string;
  is_paired: boolean;
}

export interface LogicalThreadDetail extends LogicalThreadRead {
  bindings: ThreadChannelBindingRead[];
}

export interface LogicalThreadList {
  items: LogicalThreadRead[];
  total: number;
}

export interface ThreadActiveSession {
  thread_id: string;
  session_id: string;
  last_activity_at: string;
}

export interface PairingInitiateBody {
  source_channel_id?: string | null;
  source_external_user_id?: string | null;
  target_channel_id?: string | null;
  target_external_user_id?: string | null;
}

export interface PairingInitiateResponse {
  code: string;
  expires_at: string;
  ttl_seconds: number;
}

export interface PairingConsumeBody {
  code: string;
  channel_id?: string | null;
  external_user_id?: string | null;
}

export interface PairingConsumeResponse {
  thread_id: string;
  primary_session_id: string;
  bindings_paired: number;
  threads_merged: number;
}

const _root = "/api/v1/threads";

export function useLogicalThreads(params?: { limit?: number; offset?: number }) {
  const tok = useAuthStore((s) => s.accessToken);
  const ws = useWorkspaceStore((s) => s.activeWorkspaceId);
  const limit = params?.limit ?? 50;
  const offset = params?.offset ?? 0;
  return useQuery<LogicalThreadList>({
    queryKey: ["logical-threads", ws, limit, offset],
    queryFn: () =>
      api.get<LogicalThreadList>(
        `${_root}?limit=${limit}&offset=${offset}`,
      ),
    enabled: Boolean(tok && ws),
    staleTime: 15_000,
  });
}

export function useLogicalThread(threadId: string | null | undefined) {
  const tok = useAuthStore((s) => s.accessToken);
  const ws = useWorkspaceStore((s) => s.activeWorkspaceId);
  return useQuery<LogicalThreadDetail>({
    queryKey: ["logical-thread", ws, threadId],
    queryFn: () => api.get<LogicalThreadDetail>(`${_root}/${threadId}`),
    enabled: Boolean(tok && ws && threadId),
  });
}

export function useThreadActiveSession(threadId: string | null | undefined) {
  const tok = useAuthStore((s) => s.accessToken);
  const ws = useWorkspaceStore((s) => s.activeWorkspaceId);
  return useQuery<ThreadActiveSession>({
    queryKey: ["logical-thread-active-session", ws, threadId],
    queryFn: () =>
      api.get<ThreadActiveSession>(`${_root}/${threadId}/sessions/active`),
    enabled: Boolean(tok && ws && threadId),
  });
}

export function useThreadBindings(threadId: string | null | undefined) {
  const tok = useAuthStore((s) => s.accessToken);
  const ws = useWorkspaceStore((s) => s.activeWorkspaceId);
  return useQuery<ThreadChannelBindingRead[]>({
    queryKey: ["logical-thread-bindings", ws, threadId],
    queryFn: () =>
      api.get<ThreadChannelBindingRead[]>(`${_root}/${threadId}/bindings`),
    enabled: Boolean(tok && ws && threadId),
  });
}

export function useRelabelThread(threadId: string | null | undefined) {
  const qc = useQueryClient();
  const ws = useWorkspaceStore((s) => s.activeWorkspaceId);
  return useMutation<LogicalThreadRead, unknown, { label: string | null }>({
    mutationFn: (body) =>
      api.post<LogicalThreadRead>(`${_root}/${threadId}/label`, body),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["logical-threads", ws] });
      qc.invalidateQueries({ queryKey: ["logical-thread", ws, threadId] });
    },
  });
}

export function useInitiatePairing() {
  return useMutation<PairingInitiateResponse, unknown, PairingInitiateBody>({
    mutationFn: (body) =>
      api.post<PairingInitiateResponse>(`${_root}/pair/initiate`, body),
  });
}

export function useConsumePairing() {
  const qc = useQueryClient();
  const ws = useWorkspaceStore((s) => s.activeWorkspaceId);
  return useMutation<PairingConsumeResponse, unknown, PairingConsumeBody>({
    mutationFn: (body) =>
      api.post<PairingConsumeResponse>(`${_root}/pair/consume`, body),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["logical-threads", ws] });
    },
  });
}

export function useUnbindChannel(threadId: string | null | undefined) {
  const qc = useQueryClient();
  const ws = useWorkspaceStore((s) => s.activeWorkspaceId);
  return useMutation<void, unknown, { binding_id: string }>({
    mutationFn: ({ binding_id }) =>
      api.delete<void>(`${_root}/${threadId}/bindings/${binding_id}`),
    onSuccess: () => {
      qc.invalidateQueries({
        queryKey: ["logical-thread-bindings", ws, threadId],
      });
      qc.invalidateQueries({ queryKey: ["logical-thread", ws, threadId] });
    },
  });
}
