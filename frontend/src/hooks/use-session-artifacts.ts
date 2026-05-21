"use client";

import { useQuery } from "@tanstack/react-query";

import { api } from "@/lib/api";
import { useAuthStore } from "@/stores/auth-store";
import { useWorkspaceStore } from "@/stores/workspace-store";
import type { SessionArtifactRead } from "@/types/api";

const sessionArtifactsKey = (ws: string | null, sid: string | null) =>
  ["sessions", "artifacts", ws, sid] as const;

export function useSessionArtifacts(
  sessionId: string | null,
  options?: { limit?: number; enabled?: boolean },
) {
  const token = useAuthStore((s) => s.accessToken);
  const ws = useWorkspaceStore((s) => s.activeWorkspaceId);
  const limit = options?.limit ?? 5;
  const enabled =
    Boolean(token && ws && sessionId) && (options?.enabled ?? true);
  return useQuery<SessionArtifactRead[]>({
    queryKey: [...sessionArtifactsKey(ws, sessionId), limit],
    queryFn: () =>
      api.get<SessionArtifactRead[]>(
        `/api/v1/sessions/${sessionId}/artifacts?limit=${limit}`,
      ),
    enabled,
    staleTime: 10_000,
    refetchInterval: 15_000,
  });
}
