"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "@/lib/api";
import { useAuthStore } from "@/stores/auth-store";
import { useWorkspaceStore } from "@/stores/workspace-store";
import type {
  PublicSharedSession,
  SessionRead,
  SessionShareList,
  SessionShareRead,
  SharePermission,
  ShareVisibility,
} from "@/types/api";

export function useSessionShares(sessionId: string | null) {
  const token = useAuthStore((s) => s.accessToken);
  const ws = useWorkspaceStore((s) => s.activeWorkspaceId);
  return useQuery<SessionShareList>({
    queryKey: ["sessions", "shares", ws, sessionId],
    queryFn: () =>
      api.get<SessionShareList>(`/api/v1/sessions/${sessionId}/shares`),
    enabled: Boolean(token && ws && sessionId),
    staleTime: 15_000,
  });
}

export interface CreateShareArgs {
  sessionId: string;
  shared_with?: string | null;
  generate_link?: boolean;
  permission?: SharePermission;
  visibility?: ShareVisibility;
  expires_at?: string | null;
}

export function useCreateShare() {
  const qc = useQueryClient();
  return useMutation<SessionShareRead, Error, CreateShareArgs>({
    mutationFn: ({ sessionId, ...rest }) =>
      api.post<SessionShareRead>(
        `/api/v1/sessions/${sessionId}/shares`,
        {
          shared_with: rest.shared_with ?? null,
          generate_link: rest.generate_link ?? false,
          permission: rest.permission ?? "view",
          visibility: rest.visibility ?? "workspace",
          expires_at: rest.expires_at ?? null,
        },
      ),
    onSuccess: (_data, vars) => {
      qc.invalidateQueries({
        queryKey: ["sessions", "shares"],
        predicate: (q) =>
          Array.isArray(q.queryKey) &&
          (q.queryKey as unknown[])[0] === "sessions" &&
          (q.queryKey as unknown[])[3] === vars.sessionId,
      });
    },
  });
}

interface RevokeShareArgs {
  sessionId: string;
  shareId: string;
}

export function useRevokeShare() {
  const qc = useQueryClient();
  return useMutation<void, Error, RevokeShareArgs>({
    mutationFn: ({ sessionId, shareId }) =>
      api.delete(`/api/v1/sessions/${sessionId}/shares/${shareId}`),
    onSuccess: (_data, vars) => {
      qc.invalidateQueries({
        queryKey: ["sessions", "shares"],
        predicate: (q) =>
          Array.isArray(q.queryKey) &&
          (q.queryKey as unknown[])[0] === "sessions" &&
          (q.queryKey as unknown[])[3] === vars.sessionId,
      });
    },
  });
}

export function useSessionsSharedWithMe(
  enabled = true,
  limit = 50,
) {
  const token = useAuthStore((s) => s.accessToken);
  return useQuery<SessionRead[]>({
    queryKey: ["sessions", "shared-with-me", limit],
    queryFn: () =>
      api.get<SessionRead[]>(
        `/api/v1/sessions/shared-with-me?limit=${limit}`,
      ),
    enabled: Boolean(token) && enabled,
    staleTime: 30_000,
  });
}

/**
 * Public share-token resolver. Doesn't use the auth store — the endpoint is
 * unauthenticated by design so the page works for anyone with the link.
 */
export async function fetchPublicShare(
  token: string,
): Promise<PublicSharedSession> {
  return api.get<PublicSharedSession>(
    `/api/v1/sessions/shared/${encodeURIComponent(token)}`,
    { skipAuth: true },
  );
}
