"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";

import { api, ApiError } from "@/lib/api";
import { useAuthStore } from "@/stores/auth-store";
import { useWorkspaceStore } from "@/stores/workspace-store";
import type {
  JudgeSessionSummary,
  JudgeVerdictRead,
  SessionArtifactRead,
} from "@/types/api";

const verdictKey = (ws: string | null, artifactId: string | null) =>
  ["judge", "verdict", ws, artifactId] as const;

const summaryKey = (ws: string | null, sessionId: string | null) =>
  ["judge", "summary", ws, sessionId] as const;

export function useArtifactVerdict(
  artifactId: string | null,
  options?: { enabled?: boolean },
) {
  const token = useAuthStore((s) => s.accessToken);
  const ws = useWorkspaceStore((s) => s.activeWorkspaceId);
  const enabled =
    Boolean(token && ws && artifactId) && (options?.enabled ?? true);
  return useQuery<JudgeVerdictRead | null>({
    queryKey: verdictKey(ws, artifactId),
    queryFn: async () => {
      try {
        return await api.get<JudgeVerdictRead>(
          `/api/v1/artifacts/${artifactId}/verdict`,
        );
      } catch (err) {
        if (err instanceof ApiError && err.status === 404) return null;
        throw err;
      }
    },
    enabled,
    staleTime: 15_000,
  });
}

export function useSessionJudgeSummary(
  sessionId: string | null,
  options?: { enabled?: boolean; refetchInterval?: number | false },
) {
  const token = useAuthStore((s) => s.accessToken);
  const ws = useWorkspaceStore((s) => s.activeWorkspaceId);
  const enabled =
    Boolean(token && ws && sessionId) && (options?.enabled ?? true);
  return useQuery<JudgeSessionSummary>({
    queryKey: summaryKey(ws, sessionId),
    queryFn: () =>
      api.get<JudgeSessionSummary>(
        `/api/v1/sessions/${sessionId}/artifacts/judge-summary`,
      ),
    enabled,
    staleTime: 10_000,
    refetchInterval: options?.refetchInterval ?? 30_000,
  });
}

export function useRejudgeArtifact() {
  const qc = useQueryClient();
  const ws = useWorkspaceStore((s) => s.activeWorkspaceId);
  return useMutation({
    mutationFn: (artifactId: string) =>
      api.post<SessionArtifactRead>(
        `/api/v1/artifacts/${artifactId}/rejudge`,
      ),
    onSuccess: (artifact) => {
      qc.invalidateQueries({
        queryKey: verdictKey(ws, artifact.id),
      });
      qc.invalidateQueries({ queryKey: ["sessions", "artifacts"] });
      qc.invalidateQueries({
        queryKey: summaryKey(ws, artifact.session_id),
      });
    },
    onError: (err) => {
      const message =
        err instanceof ApiError ? err.message : "rejudge_failed";
      toast.error(message);
    },
  });
}
