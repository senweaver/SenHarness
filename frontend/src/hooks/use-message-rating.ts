"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "@/lib/api";
import { useAuthStore } from "@/stores/auth-store";
import { useWorkspaceStore } from "@/stores/workspace-store";
import type {
  MessageRatingRead,
  MessageRatingSummary,
  RatingValue,
} from "@/types/api";

export type { RatingValue, MessageRatingSummary } from "@/types/api";

/** Fetch aggregate rating summary for every assistant message in a session. */
export function useSessionRatings(sessionId: string | null) {
  const token = useAuthStore((s) => s.accessToken);
  const ws = useWorkspaceStore((s) => s.activeWorkspaceId);
  return useQuery<MessageRatingSummary[]>({
    queryKey: ["sessions", "ratings", ws, sessionId],
    queryFn: () =>
      api.get<MessageRatingSummary[]>(
        `/api/v1/sessions/${sessionId}/ratings`,
      ),
    enabled: Boolean(token && ws && sessionId),
    staleTime: 30_000,
  });
}

interface RateArgs {
  sessionId: string;
  messageId: string;
  rating: RatingValue;
  comment?: string | null;
}

/** Upsert / remove the caller's rating on an assistant message. */
export function useRateMessage() {
  const qc = useQueryClient();
  return useMutation<MessageRatingRead, Error, RateArgs>({
    mutationFn: ({ sessionId, messageId, rating, comment }) =>
      api.post<MessageRatingRead>(
        `/api/v1/sessions/${sessionId}/messages/${messageId}/rate`,
        { rating, comment: comment ?? null },
      ),
    onSuccess: (_data, vars) => {
      qc.invalidateQueries({
        queryKey: ["sessions", "ratings"],
        predicate: (q) =>
          Array.isArray(q.queryKey) &&
          (q.queryKey as unknown[])[0] === "sessions" &&
          (q.queryKey as unknown[])[3] === vars.sessionId,
      });
    },
  });
}

interface UnrateArgs {
  sessionId: string;
  messageId: string;
}

export function useRemoveRating() {
  const qc = useQueryClient();
  return useMutation<void, Error, UnrateArgs>({
    mutationFn: ({ sessionId, messageId }) =>
      api.delete(
        `/api/v1/sessions/${sessionId}/messages/${messageId}/rate`,
      ),
    onSuccess: (_data, vars) => {
      qc.invalidateQueries({
        queryKey: ["sessions", "ratings"],
        predicate: (q) =>
          Array.isArray(q.queryKey) &&
          (q.queryKey as unknown[])[0] === "sessions" &&
          (q.queryKey as unknown[])[3] === vars.sessionId,
      });
    },
  });
}
