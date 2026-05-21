"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api } from "@/lib/api";
import { useAuthStore } from "@/stores/auth-store";
import { useWorkspaceStore } from "@/stores/workspace-store";
import type {
  GoalAlignmentScoreRead,
  SessionGoalCreate,
  SessionGoalRead,
  SessionGoalUpdate,
} from "@/types/api";

const goalsKey = (ws: string | null, sid: string | null) =>
  ["sessions", "goals", ws, sid] as const;
const alignmentKey = (ws: string | null, sid: string | null) =>
  ["sessions", "alignment", ws, sid] as const;

/** Active (unlocked_at IS NULL) goal for a session, or null. */
export function useActiveSessionGoal(sessionId: string | null) {
  const token = useAuthStore((s) => s.accessToken);
  const ws = useWorkspaceStore((s) => s.activeWorkspaceId);
  return useQuery<SessionGoalRead | null>({
    queryKey: goalsKey(ws, sessionId),
    queryFn: async () => {
      const rows = await api.get<SessionGoalRead[]>(
        `/api/v1/sessions/${sessionId}/goals?only_active=true`,
      );
      return rows[0] ?? null;
    },
    enabled: Boolean(token && ws && sessionId),
    staleTime: 30_000,
  });
}

/** Per-message alignment score rows for the session, ascending by created_at. */
export function useSessionAlignment(sessionId: string | null) {
  const token = useAuthStore((s) => s.accessToken);
  const ws = useWorkspaceStore((s) => s.activeWorkspaceId);
  return useQuery<GoalAlignmentScoreRead[]>({
    queryKey: alignmentKey(ws, sessionId),
    queryFn: () =>
      api.get<GoalAlignmentScoreRead[]>(
        `/api/v1/sessions/${sessionId}/alignment`,
      ),
    enabled: Boolean(token && ws && sessionId),
    staleTime: 5_000,
    refetchInterval: 8_000,
  });
}

interface LockArgs {
  sessionId: string;
  body: SessionGoalCreate;
}

export function useLockGoal() {
  const qc = useQueryClient();
  return useMutation<SessionGoalRead, Error, LockArgs>({
    mutationFn: ({ sessionId, body }) =>
      api.post<SessionGoalRead>(
        `/api/v1/sessions/${sessionId}/goals`,
        body,
      ),
    onSuccess: (_data, vars) => {
      qc.invalidateQueries({
        queryKey: ["sessions", "goals"],
        predicate: (q) =>
          Array.isArray(q.queryKey) &&
          (q.queryKey as unknown[])[0] === "sessions" &&
          (q.queryKey as unknown[])[3] === vars.sessionId,
      });
    },
  });
}

interface UpdateArgs {
  sessionId: string;
  goalId: string;
  body: SessionGoalUpdate;
}

export function useUpdateGoal() {
  const qc = useQueryClient();
  return useMutation<SessionGoalRead, Error, UpdateArgs>({
    mutationFn: ({ sessionId, goalId, body }) =>
      api.patch<SessionGoalRead>(
        `/api/v1/sessions/${sessionId}/goals/${goalId}`,
        body,
      ),
    onSuccess: (_data, vars) => {
      qc.invalidateQueries({
        queryKey: ["sessions", "goals"],
        predicate: (q) =>
          Array.isArray(q.queryKey) &&
          (q.queryKey as unknown[])[0] === "sessions" &&
          (q.queryKey as unknown[])[3] === vars.sessionId,
      });
    },
  });
}

interface UnlockArgs {
  sessionId: string;
  goalId: string;
}

export function useUnlockGoal() {
  const qc = useQueryClient();
  return useMutation<SessionGoalRead, Error, UnlockArgs>({
    mutationFn: ({ sessionId, goalId }) =>
      api.post<SessionGoalRead>(
        `/api/v1/sessions/${sessionId}/goals/${goalId}/unlock`,
      ),
    onSuccess: (_data, vars) => {
      qc.invalidateQueries({
        queryKey: ["sessions", "goals"],
        predicate: (q) =>
          Array.isArray(q.queryKey) &&
          (q.queryKey as unknown[])[0] === "sessions" &&
          (q.queryKey as unknown[])[3] === vars.sessionId,
      });
    },
  });
}

interface RealignArgs {
  sessionId: string;
  messageId: string;
}

export function useRealignMessage() {
  const qc = useQueryClient();
  return useMutation<GoalAlignmentScoreRead | null, Error, RealignArgs>({
    mutationFn: ({ sessionId, messageId }) =>
      api.post<GoalAlignmentScoreRead | null>(
        `/api/v1/sessions/${sessionId}/messages/${messageId}/realign`,
      ),
    onSuccess: (_data, vars) => {
      qc.invalidateQueries({
        queryKey: ["sessions", "alignment"],
        predicate: (q) =>
          Array.isArray(q.queryKey) &&
          (q.queryKey as unknown[])[0] === "sessions" &&
          (q.queryKey as unknown[])[3] === vars.sessionId,
      });
    },
  });
}
