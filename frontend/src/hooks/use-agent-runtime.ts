"use client";

import { useEffect, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";

import { api } from "@/lib/api";
import { useAuthStore } from "@/stores/auth-store";
import { useWorkspaceStore } from "@/stores/workspace-store";

export interface RuntimeRunCard {
  session_id: string;
  agent_id: string | null;
  agent_name: string | null;
  agent_avatar_url: string | null;
  user_name: string | null;
  run_id: string;
  state: string;
  current_phase: string | null;
  running_tool_name: string | null;
  first_token_received: boolean;
  queue_len: number;
  age_ms: number;
  ms_since_last_event: number;
  stuck_reason: "idle_silent" | "tool_silent" | "hard_cap" | null;
  orphan: boolean;
  subagent_count: number;
}

export interface RuntimeSnapshot {
  summary: {
    running: number;
    stuck: number;
    orphan: number;
    queued: number;
    subagents_active: number;
  };
  runs: RuntimeRunCard[];
  subagents: Array<{
    parent_run_id: string;
    name: string;
    state: string;
  }>;
  timestamp: number;
}

const KEY = ["agent-runtime", "snapshot"] as const;

export function useAgentRuntimeSnapshot() {
  const token = useAuthStore((s) => s.accessToken);
  const ws = useWorkspaceStore((s) => s.activeWorkspaceId);
  return useQuery<RuntimeSnapshot>({
    queryKey: [...KEY, ws],
    queryFn: () =>
      api.get<RuntimeSnapshot>("/api/v1/agent-runtime/snapshot"),
    enabled: Boolean(token && ws),
    refetchInterval: 30_000,
    staleTime: 5_000,
  });
}

export interface AgentRuntimeWsOptions {
  /** Optional list of workspace ids to multiplex into one socket.
   *  When omitted, the backend falls back to the JWT ``ws`` claim
   *  (legacy Agent View behaviour). */
  subscribeWorkspaces?: string[];
}

export function resolveAgentRuntimeWsUrl(
  token: string,
  options: AgentRuntimeWsOptions = {},
): string {
  const base =
    process.env.NEXT_PUBLIC_WS_BASE_URL?.replace(/\/$/, "") ??
    "ws://localhost:8000";
  let host = base;
  if (typeof window !== "undefined") {
    try {
      const parsed = new URL(base);
      const isLoopback =
        parsed.hostname === "localhost" || parsed.hostname === "127.0.0.1";
      const browserHost = window.location.hostname;
      if (
        isLoopback &&
        browserHost &&
        browserHost !== "localhost" &&
        browserHost !== "127.0.0.1"
      ) {
        parsed.hostname = browserHost;
        host = parsed.toString().replace(/\/$/, "");
      }
    } catch {
      /* fall through to default */
    }
  }
  const params = new URLSearchParams({ token });
  if (options.subscribeWorkspaces && options.subscribeWorkspaces.length > 0) {
    params.set(
      "subscribe_workspaces",
      options.subscribeWorkspaces.join(","),
    );
  }
  return `${host}/api/v1/agent-runtime/ws?${params.toString()}`;
}

/** Subscribe to ``runtime.run_card_update`` frames and invalidate the
 *  snapshot query whenever a card update lands. */
export function useAgentRuntimeStream() {
  const token = useAuthStore((s) => s.accessToken);
  const ws = useWorkspaceStore((s) => s.activeWorkspaceId);
  const qc = useQueryClient();
  const [status, setStatus] = useState<
    "idle" | "connecting" | "open" | "closed" | "error"
  >("idle");

  useEffect(() => {
    if (!token || !ws) return;
    setStatus("connecting");
    const url = resolveAgentRuntimeWsUrl(token);
    const socket = new WebSocket(url);
    socket.addEventListener("open", () => setStatus("open"));
    socket.addEventListener("close", () => setStatus("closed"));
    socket.addEventListener("error", () => setStatus("error"));
    socket.addEventListener("message", () => {
      qc.invalidateQueries({ queryKey: [...KEY, ws] });
    });
    return () => {
      try {
        socket.close();
      } catch {
        /* ignore */
      }
    };
  }, [token, ws, qc]);

  return { status };
}
