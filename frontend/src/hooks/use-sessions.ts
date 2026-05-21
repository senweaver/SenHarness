"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api } from "@/lib/api";
import { useAuthStore } from "@/stores/auth-store";
import { useWorkspaceStore } from "@/stores/workspace-store";
import type { SessionRead } from "@/types/api";

export function useRecentSessions(limit = 10) {
  const token = useAuthStore((s) => s.accessToken);
  const ws = useWorkspaceStore((s) => s.activeWorkspaceId);
  return useQuery<SessionRead[]>({
    queryKey: ["sessions", "recent", ws, limit],
    queryFn: () => api.get<SessionRead[]>(`/api/v1/sessions?limit=${limit}`),
    enabled: Boolean(token && ws),
  });
}

/**
 * Fetch a single session by id. Used by the chat header to resolve which
 * agent (or squad) owns the active conversation; we don't piggy-back on
 * `useRecentSessions` because the active session may have been archived
 * out of the recent list.
 */
export function useSession(sessionId: string | undefined | null) {
  const token = useAuthStore((s) => s.accessToken);
  const ws = useWorkspaceStore((s) => s.activeWorkspaceId);
  return useQuery<SessionRead>({
    queryKey: ["sessions", "detail", ws, sessionId],
    queryFn: () => api.get<SessionRead>(`/api/v1/sessions/${sessionId}`),
    enabled: Boolean(token && ws && sessionId),
  });
}

export interface UpdateSessionInput {
  sessionId: string;
  title?: string | null;
  state?: "active" | "archived";
}

/**
 * Patch a session's title/state. Used by the inline rename dialog and by
 * the archive action in the session row dropdown.
 */
export function useUpdateSession() {
  const qc = useQueryClient();
  return useMutation<SessionRead, unknown, UpdateSessionInput>({
    mutationFn: ({ sessionId, ...body }) =>
      api.patch<SessionRead>(`/api/v1/sessions/${sessionId}`, body),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["sessions"] });
    },
  });
}

/**
 * Soft-delete a session (the backend marks it deleted; row no longer
 * appears in `/sessions` listings).
 */
export function useDeleteSession() {
  const qc = useQueryClient();
  return useMutation<void, unknown, { sessionId: string }>({
    mutationFn: ({ sessionId }) =>
      api.delete<void>(`/api/v1/sessions/${sessionId}`),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["sessions"] });
    },
  });
}
