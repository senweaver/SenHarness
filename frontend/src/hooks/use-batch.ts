"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api } from "@/lib/api";

export interface SessionCheckpoint {
  id: string;
  workspace_id: string;
  session_id: string;
  label: string;
  description: string | null;
  message_count: number;
  snapshot_json: Record<string, unknown>;
  created_by: string | null;
  created_at: string;
  updated_at: string;
}

export interface SessionForkResult {
  original_session_id: string;
  fork_session_id: string;
  copied_message_count: number;
}

export interface BatchRun {
  id: string;
  workspace_id: string;
  name: string;
  description: string | null;
  agent_id: string | null;
  status:
    | "pending"
    | "running"
    | "succeeded"
    | "failed"
    | "cancelled";
  config_json: Record<string, unknown>;
  stats_json: Record<string, unknown>;
  started_at: string | null;
  finished_at: string | null;
  created_by: string | null;
  created_at: string;
  updated_at: string;
}

export interface BatchRunCase {
  id: string;
  workspace_id: string;
  batch_run_id: string;
  case_label: string | null;
  input_text: string;
  source_session_id: string | null;
  checkpoint_id: string | null;
  replay_session_id: string | null;
  status: "pending" | "running" | "succeeded" | "failed" | "skipped";
  baseline_text: string | null;
  output_text: string | null;
  diff_json: {
    similarity?: number;
    unified_diff?: string;
    baseline_tokens?: number;
    candidate_tokens?: number;
  };
  error: string | null;
  duration_ms: number | null;
  created_at: string;
  updated_at: string;
}

export interface BatchRunDetail extends BatchRun {
  cases: BatchRunCase[];
}

export interface BatchCaseInput {
  label?: string;
  text?: string;
  source_session_id?: string;
  checkpoint_id?: string;
}

export interface BatchRunCreateInput {
  name: string;
  description?: string;
  agent_id: string;
  cases: BatchCaseInput[];
  config_json?: Record<string, unknown>;
}

// ─── Session checkpoints ─────────────────────────────────
export function useSessionCheckpoints(sessionId: string | null | undefined) {
  return useQuery<SessionCheckpoint[]>({
    queryKey: ["session-checkpoints", sessionId],
    queryFn: () =>
      api.get<SessionCheckpoint[]>(
        `/api/v1/sessions/${sessionId}/checkpoints`,
      ),
    enabled: Boolean(sessionId),
  });
}

export function useCreateSessionCheckpoint(sessionId: string) {
  const qc = useQueryClient();
  return useMutation<
    SessionCheckpoint,
    unknown,
    { label: string; description?: string }
  >({
    mutationFn: (input) =>
      api.post<SessionCheckpoint>(
        `/api/v1/sessions/${sessionId}/checkpoints`,
        input,
      ),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["session-checkpoints", sessionId] });
    },
  });
}

export function useDeleteSessionCheckpoint(sessionId: string) {
  const qc = useQueryClient();
  return useMutation<void, unknown, string>({
    mutationFn: (checkpointId) =>
      api.delete(
        `/api/v1/sessions/${sessionId}/checkpoints/${checkpointId}`,
      ),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["session-checkpoints", sessionId] });
    },
  });
}

export function useForkSession(sessionId: string) {
  return useMutation<
    SessionForkResult,
    unknown,
    { checkpoint_id: string; title?: string }
  >({
    mutationFn: (input) =>
      api.post<SessionForkResult>(
        `/api/v1/sessions/${sessionId}/fork`,
        input,
      ),
  });
}

// ─── Batch runs ──────────────────────────────────────────
const LIST_KEY = ["batch-runs"] as const;

export function useBatchRuns() {
  return useQuery<BatchRun[]>({
    queryKey: LIST_KEY,
    queryFn: () => api.get<BatchRun[]>("/api/v1/batch/runs"),
  });
}

export function useBatchRun(batchRunId: string | null | undefined) {
  return useQuery<BatchRunDetail>({
    queryKey: ["batch-runs", batchRunId],
    queryFn: () =>
      api.get<BatchRunDetail>(`/api/v1/batch/runs/${batchRunId}`),
    enabled: Boolean(batchRunId),
    refetchInterval: (data) => {
      const d = data as unknown as BatchRunDetail | undefined;
      if (!d) return 3000;
      return d.status === "running" || d.status === "pending" ? 2000 : false;
    },
  });
}

export function useCreateBatchRun() {
  const qc = useQueryClient();
  return useMutation<BatchRun, unknown, BatchRunCreateInput>({
    mutationFn: (input) => api.post<BatchRun>("/api/v1/batch/runs", input),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: LIST_KEY });
    },
  });
}
