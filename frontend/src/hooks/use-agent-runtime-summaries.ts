"use client";

import { useEffect, useMemo } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";

import { api } from "@/lib/api";
import { useAuthStore } from "@/stores/auth-store";
import { useWorkspaceStore } from "@/stores/workspace-store";
import type {
  WorkspaceRuntimeSummary,
  WorkspaceRuntimeSummariesResponse,
} from "@/types/api";

import { resolveAgentRuntimeWsUrl } from "./use-agent-runtime";

const SUMMARIES_KEY = ["agent-runtime", "summaries"] as const;

/** Cross-workspace runtime counters for the workspace switcher.
 *
 *  Returns one entry per workspace the caller belongs to (server-side
 *  hard-capped). Refetches every 60s as a safety net behind the WS
 *  push stream powered by ``useAgentRuntimeSummariesStream``.
 */
export function useAgentRuntimeSummaries() {
  const token = useAuthStore((s) => s.accessToken);
  return useQuery<WorkspaceRuntimeSummariesResponse>({
    queryKey: [...SUMMARIES_KEY],
    queryFn: () =>
      api.get<WorkspaceRuntimeSummariesResponse>(
        "/api/v1/agent-runtime/summaries",
      ),
    enabled: Boolean(token),
    refetchInterval: 60_000,
    staleTime: 5_000,
  });
}

/** Multiplexed WS subscription that patches the summaries cache when
 *  a ``runtime.workspace_summary`` frame arrives, and invalidates on
 *  ``runtime.run_card_update`` frames. Noops when token is missing or
 *  the workspace list is empty.
 */
export function useAgentRuntimeSummariesStream() {
  const token = useAuthStore((s) => s.accessToken);
  const workspaces = useWorkspaceStore((s) => s.workspaces);
  const qc = useQueryClient();
  const workspaceIds = useMemo(
    () => workspaces.map((w) => w.id),
    [workspaces],
  );
  const idKey = workspaceIds.join(",");

  useEffect(() => {
    if (!token || workspaceIds.length === 0) return;
    const url = resolveAgentRuntimeWsUrl(token, {
      subscribeWorkspaces: workspaceIds,
    });
    const socket = new WebSocket(url);
    socket.addEventListener("message", (event) => {
      let parsed: { type?: string; data?: Record<string, unknown> };
      try {
        parsed = JSON.parse(event.data as string) as typeof parsed;
      } catch {
        return;
      }
      if (parsed.type === "runtime.workspace_summary" && parsed.data) {
        const data = parsed.data as Partial<WorkspaceRuntimeSummary>;
        if (typeof data.workspace_id !== "string") return;
        const next: WorkspaceRuntimeSummary = {
          workspace_id: data.workspace_id,
          running: Number(data.running ?? 0),
          stuck: Number(data.stuck ?? 0),
          orphan: Number(data.orphan ?? 0),
          queued: Number(data.queued ?? 0),
        };
        qc.setQueryData<WorkspaceRuntimeSummariesResponse | undefined>(
          [...SUMMARIES_KEY],
          (prev) => {
            if (!prev) return prev;
            const idx = prev.summaries.findIndex(
              (s) => s.workspace_id === next.workspace_id,
            );
            const summaries =
              idx === -1
                ? [...prev.summaries, next]
                : prev.summaries.map((s, i) => (i === idx ? next : s));
            return { ...prev, summaries };
          },
        );
        return;
      }
      if (parsed.type === "runtime.run_card_update") {
        qc.invalidateQueries({ queryKey: [...SUMMARIES_KEY] });
      }
    });
    return () => {
      try {
        socket.close();
      } catch {
        /* ignore */
      }
    };
  }, [token, idKey, qc, workspaceIds]);
}
