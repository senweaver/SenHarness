"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";

import { api, ApiError } from "@/lib/api";
import { useAuthStore } from "@/stores/auth-store";
import { useWorkspaceStore } from "@/stores/workspace-store";
import type {
  PendingMemoryRead,
  PendingMemoryStats,
  PromoteSweepResult,
} from "@/types/api";

const sessionKey = (ws: string | null, sessionId: string | null) =>
  ["pending-memories", ws, "session", sessionId] as const;

const statsKey = (ws: string | null, workspaceId: string | null) =>
  ["pending-memories", ws, "stats", workspaceId] as const;

export function useSessionPendingMemories(
  sessionId: string | null,
  options?: { enabled?: boolean; refetchInterval?: number | false },
) {
  const token = useAuthStore((s) => s.accessToken);
  const ws = useWorkspaceStore((s) => s.activeWorkspaceId);
  const enabled =
    Boolean(token && ws && sessionId) && (options?.enabled ?? true);
  return useQuery<PendingMemoryRead[]>({
    queryKey: sessionKey(ws, sessionId),
    queryFn: () =>
      api.get<PendingMemoryRead[]>(
        `/api/v1/sessions/${sessionId}/pending-memories`,
      ),
    enabled,
    staleTime: 10_000,
    refetchInterval: options?.refetchInterval ?? false,
  });
}

export function useCancelPendingMemory(sessionId: string | null) {
  const qc = useQueryClient();
  const ws = useWorkspaceStore((s) => s.activeWorkspaceId);
  return useMutation({
    mutationFn: (pendingId: string) =>
      api.post<PendingMemoryRead>(
        `/api/v1/sessions/${sessionId}/pending-memories/${pendingId}/cancel`,
      ),
    onSuccess: (row) => {
      qc.invalidateQueries({
        queryKey: sessionKey(ws, row.session_id),
      });
      qc.invalidateQueries({
        queryKey: statsKey(ws, row.workspace_id),
      });
    },
    onError: (err) => {
      const message =
        err instanceof ApiError ? err.message : "pending_memory_cancel_failed";
      toast.error(message);
    },
  });
}

export function useWorkspacePendingMemoryStats(
  workspaceId: string | null,
  options?: { enabled?: boolean; refetchInterval?: number | false },
) {
  const token = useAuthStore((s) => s.accessToken);
  const ws = useWorkspaceStore((s) => s.activeWorkspaceId);
  const enabled =
    Boolean(token && ws && workspaceId) && (options?.enabled ?? true);
  return useQuery<PendingMemoryStats>({
    queryKey: statsKey(ws, workspaceId),
    queryFn: () =>
      api.get<PendingMemoryStats>(
        `/api/v1/workspaces/${workspaceId}/pending-memories/stats`,
      ),
    enabled,
    staleTime: 30_000,
    refetchInterval: options?.refetchInterval ?? false,
  });
}

export function useTriggerPendingMemorySweep() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () =>
      api.post<PromoteSweepResult>(
        `/api/v1/admin/pending-memories/promote-now`,
      ),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["pending-memories"] });
    },
    onError: (err) => {
      const message =
        err instanceof ApiError ? err.message : "pending_memory_trigger_failed";
      toast.error(message);
    },
  });
}
