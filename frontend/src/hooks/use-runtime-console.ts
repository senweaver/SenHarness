"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api } from "@/lib/api";
import { useAuthStore } from "@/stores/auth-store";
import { useWorkspaceStore } from "@/stores/workspace-store";
import type {
  ForceRecycleResult,
  InflightRunListOut,
  InflightRunStateBucket,
  RuntimeConsoleStats,
} from "@/types/api";

const POLL_INTERVAL_MS = 5_000;

interface UseInflightRunsOptions {
  states?: InflightRunStateBucket[];
  limit?: number;
}

function buildStateParam(states: InflightRunStateBucket[] | undefined) {
  if (!states || states.length === 0) return "";
  return `state=${states.join(",")}`;
}

export function useInflightRuns(options: UseInflightRunsOptions = {}) {
  const token = useAuthStore((s) => s.accessToken);
  const workspaceId = useWorkspaceStore((s) => s.activeWorkspaceId);
  const stateParam = buildStateParam(options.states);
  const limit = options.limit ?? 200;
  const search = new URLSearchParams();
  search.set("limit", String(limit));
  if (stateParam) {
    const [, value] = stateParam.split("=");
    search.set("state", value);
  }

  return useQuery<InflightRunListOut>({
    queryKey: [
      "runtime-console",
      "inflight-runs",
      workspaceId,
      options.states ?? null,
      limit,
    ],
    queryFn: () =>
      api.get<InflightRunListOut>(
        `/api/v1/admin/runtime/inflight-runs?${search.toString()}`,
      ),
    enabled: Boolean(token) && Boolean(workspaceId),
    refetchInterval: POLL_INTERVAL_MS,
    refetchIntervalInBackground: false,
    staleTime: 0,
  });
}

export function useRuntimeStats() {
  const token = useAuthStore((s) => s.accessToken);
  const workspaceId = useWorkspaceStore((s) => s.activeWorkspaceId);

  return useQuery<RuntimeConsoleStats>({
    queryKey: ["runtime-console", "stats", workspaceId],
    queryFn: () =>
      api.get<RuntimeConsoleStats>("/api/v1/admin/runtime/stats"),
    enabled: Boolean(token) && Boolean(workspaceId),
    refetchInterval: POLL_INTERVAL_MS,
    refetchIntervalInBackground: false,
    staleTime: 0,
  });
}

export function useForceRecycle() {
  const qc = useQueryClient();
  return useMutation<ForceRecycleResult, unknown, { runId: string }>({
    mutationFn: ({ runId }) =>
      api.post<ForceRecycleResult>(
        `/api/v1/admin/runtime/inflight-runs/${runId}/force-recycle`,
      ),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["runtime-console"] });
    },
  });
}
