"use client";

import { useQuery } from "@tanstack/react-query";

import { api } from "@/lib/api";

// ─── Types ─────────────────────────────────────────────────────
export type TraceRole =
  | "user"
  | "assistant"
  | "system"
  | "tool_call"
  | "tool_result"
  | "thinking"
  | "approval"
  | "handoff";

export interface TraceEvent {
  message_id: string;
  role: TraceRole;
  created_at: string | null;
  content: Record<string, unknown>;
  attachments: unknown[];
  token_usage: Record<string, unknown>;
  metadata: Record<string, unknown> & {
    run_id?: string;
    eval?: {
      score?: number;
      verdict?: "pass" | "warn" | "fail";
      reasons?: string[];
      aux_model?: string | null;
      nli_agreement?: number | null;
    };
  };
  tool_call?: Record<string, unknown>;
  tool_result?: Record<string, unknown>;
  thinking?: Record<string, unknown>;
  original_turns_ref?: {
    turn_message_ids?: string[];
    turn_count?: number;
    compressed_at?: string;
    compaction_strategy?: string;
  };
  compressed_into_summary_id?: string | null;
}

export interface TraceSummary {
  tokens: { input: number; output: number };
  cost_usd: number;
  eval_verdicts: Array<Record<string, unknown> | null>;
}

export interface SessionTrace {
  session_id: string;
  title: string | null;
  agent_id: string | null;
  event_count: number;
  events: TraceEvent[];
  summary: TraceSummary;
}

export interface RunTrace {
  session_id: string;
  run_id: string;
  event_count: number;
  events: TraceEvent[];
}

// ─── Hooks ─────────────────────────────────────────────────────
/** Full chronological trace of a session. Stable while the session is open. */
export function useSessionTrace(sessionId: string | undefined, enabled = true) {
  return useQuery<SessionTrace>({
    queryKey: ["trace", "session", sessionId],
    queryFn: () =>
      api.get<SessionTrace>(
        `/api/v1/traces/sessions/${sessionId}?limit=2000`,
      ),
    enabled: !!sessionId && enabled,
  });
}

/** Narrow trace to a single run_id. */
export function useRunTrace(
  sessionId: string | undefined,
  runId: string | undefined,
  enabled = true,
) {
  return useQuery<RunTrace>({
    queryKey: ["trace", "run", sessionId, runId],
    queryFn: () =>
      api.get<RunTrace>(
        `/api/v1/traces/sessions/${sessionId}/runs/${runId}`,
      ),
    enabled: !!sessionId && !!runId && enabled,
  });
}
