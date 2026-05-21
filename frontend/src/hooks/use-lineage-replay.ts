"use client";

import { useQuery } from "@tanstack/react-query";

import { ApiError, api } from "@/lib/api";
import { useAuthStore } from "@/stores/auth-store";
import { useWorkspaceStore } from "@/stores/workspace-store";
import type { LineageReplayRead, LineageSummaryRead } from "@/types/api";

function replayKey(
  workspaceId: string | null,
  sessionId: string,
  messageId: string,
) {
  return ["lineage-replay", workspaceId, sessionId, messageId] as const;
}

function summariesKey(workspaceId: string | null, sessionId: string) {
  return ["lineage-summaries", workspaceId, sessionId] as const;
}

export function useLineageReplay(
  sessionId: string | null | undefined,
  messageId: string | null | undefined,
  options?: { enabled?: boolean },
) {
  const tok = useAuthStore((s) => s.accessToken);
  const ws = useWorkspaceStore((s) => s.activeWorkspaceId);
  const explicitlyEnabled = options?.enabled ?? true;
  return useQuery<LineageReplayRead, ApiError>({
    queryKey: replayKey(ws, sessionId ?? "", messageId ?? ""),
    queryFn: () =>
      api.get<LineageReplayRead>(
        `/api/v1/sessions/${sessionId}/messages/${messageId}/lineage`,
      ),
    enabled: Boolean(tok && ws && sessionId && messageId && explicitlyEnabled),
    retry: false,
  });
}

export function useLineageSummaries(
  sessionId: string | null | undefined,
  options?: { limit?: number },
) {
  const tok = useAuthStore((s) => s.accessToken);
  const ws = useWorkspaceStore((s) => s.activeWorkspaceId);
  const limit = Math.max(1, Math.min(options?.limit ?? 50, 200));
  return useQuery<LineageSummaryRead[], ApiError>({
    queryKey: summariesKey(ws, sessionId ?? ""),
    queryFn: () =>
      api.get<LineageSummaryRead[]>(
        `/api/v1/sessions/${sessionId}/lineage-summaries?limit=${limit}`,
      ),
    enabled: Boolean(tok && ws && sessionId),
  });
}
