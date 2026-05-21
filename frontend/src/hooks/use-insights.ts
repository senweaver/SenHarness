"use client";

/**
 * Cross-session insights hooks (M4.5).
 *
 * Two surfaces:
 *
 * - ``useGenerateInsights`` — POST ``/insights/generate``. Mostly for
 *   future surfaces that want a button outside the chat composer; the
 *   slash command path goes straight through the WebSocket and never
 *   touches this hook.
 * - ``useRecentInsights`` — GET ``/insights/recent``. Powers the
 *   "Recent insights" panel on the workspace home / chat sidebar.
 */

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api } from "@/lib/api";
import { useAuthStore } from "@/stores/auth-store";
import { useWorkspaceStore } from "@/stores/workspace-store";

export interface InsightsGenerateResponse {
  queued: boolean;
  days: number;
  expected_completion_seconds: number;
  job_id?: string | null;
}

export interface InsightsRunSummary {
  audit_event_id: string;
  session_id: string | null;
  created_at: string;
  days: number;
  artifact_count: number;
  item_count: number;
  aux_model?: string | null;
  degraded?: boolean;
}

export interface InsightsRecentResponse {
  items: InsightsRunSummary[];
}

interface GenerateArgs {
  return_session_id: string;
  days?: number | null;
}

const recentKey = (ws: string | null, days: number) =>
  ["insights", "recent", ws, days] as const;

export function useGenerateInsights() {
  const qc = useQueryClient();
  return useMutation<InsightsGenerateResponse, Error, GenerateArgs>({
    mutationFn: (body) =>
      api.post<InsightsGenerateResponse>("/api/v1/insights/generate", body),
    onSuccess: () => {
      // Invalidate any open ``recent`` query so the new run shows up
      // as soon as the audit row lands (the ARQ task writes the audit
      // synchronously after the markdown message is persisted).
      qc.invalidateQueries({
        queryKey: ["insights", "recent"],
      });
    },
  });
}

export function useRecentInsights(days: number = 30, limit: number = 10) {
  const token = useAuthStore((s) => s.accessToken);
  const ws = useWorkspaceStore((s) => s.activeWorkspaceId);
  return useQuery<InsightsRecentResponse>({
    queryKey: recentKey(ws, days),
    queryFn: () =>
      api.get<InsightsRecentResponse>(
        `/api/v1/insights/recent?days=${days}&limit=${limit}`,
      ),
    enabled: Boolean(token && ws),
    staleTime: 30_000,
  });
}
