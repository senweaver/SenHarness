"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "@/lib/api";
import { useAuthStore } from "@/stores/auth-store";
import { useWorkspaceStore } from "@/stores/workspace-store";

export type FlowTriggerKind = "cron" | "webhook" | "manual";
export type FlowRunStatus = "pending" | "running" | "succeeded" | "failed";
export type FlowExecutionMode = "agent" | "no_agent_script" | "no_agent_http";
export type FlowRunOutcome =
  | "pending"
  | "success"
  | "silent_2xx"
  | "nonempty_output"
  | "escalated_to_agent"
  | "http_error"
  | "script_error"
  | "timeout"
  | "ssrf_blocked"
  | "validation_failed"
  | "cancelled"
  | "failed";

export interface FlowRead {
  id: string;
  workspace_id: string;
  name: string;
  description: string | null;
  trigger_kind: FlowTriggerKind;
  trigger_config: Record<string, unknown>;
  execution_mode: FlowExecutionMode;
  agent_id: string | null;
  squad_id: string | null;
  prompt_template: string;
  graph_json: Record<string, unknown>;
  enabled: boolean;
  last_run_at: string | null;
  metadata_json: Record<string, unknown>;
  created_by: string | null;
  created_at: string;
  updated_at: string;
}

export interface FlowRunNodeEvent {
  node_id: string;
  type: string;
  status: "pending" | "running" | "success" | "failed";
  started_at: string | null;
  finished_at: string | null;
  input: unknown;
  output: unknown;
  error: string | null;
}

export interface FlowRunRead {
  id: string;
  workspace_id: string;
  flow_id: string;
  session_id: string | null;
  trigger_kind: FlowTriggerKind;
  trigger_payload_json: Record<string, unknown>;
  status: FlowRunStatus;
  outcome: FlowRunOutcome | null;
  started_at: string | null;
  finished_at: string | null;
  output_summary: string | null;
  error: string | null;
  probe_response_status: number | null;
  probe_duration_ms: number | null;
  probe_output_excerpt: string | null;
  node_events_json: FlowRunNodeEvent[];
  triggered_by_identity_id: string | null;
  created_at: string;
}

export interface FlowCreateInput {
  name: string;
  description?: string | null;
  trigger_kind?: FlowTriggerKind;
  trigger_config?: Record<string, unknown>;
  execution_mode?: FlowExecutionMode;
  agent_id?: string | null;
  squad_id?: string | null;
  prompt_template?: string;
  graph_json?: Record<string, unknown>;
  enabled?: boolean;
  metadata_json?: Record<string, unknown>;
}

export interface FlowTestResult {
  outcome: FlowRunOutcome;
  duration_ms: number;
  response_status?: number | null;
  exit_code?: number | null;
  output_excerpt?: string | null;
  error?: string | null;
}

export type FlowUpdateInput = Partial<FlowCreateInput>;

export function useFlows() {
  const token = useAuthStore((s) => s.accessToken);
  const ws = useWorkspaceStore((s) => s.activeWorkspaceId);
  return useQuery<FlowRead[]>({
    queryKey: ["flows", ws],
    queryFn: () => api.get<FlowRead[]>("/api/v1/flows"),
    enabled: Boolean(token && ws),
  });
}

export function useFlow(id: string | null | undefined) {
  return useQuery<FlowRead>({
    queryKey: ["flow", id],
    queryFn: () => api.get<FlowRead>(`/api/v1/flows/${id}`),
    enabled: Boolean(id),
  });
}

export function useFlowRuns(id: string | null | undefined) {
  return useQuery<FlowRunRead[]>({
    queryKey: ["flow-runs", id],
    queryFn: () => api.get<FlowRunRead[]>(`/api/v1/flows/${id}/runs`),
    enabled: Boolean(id),
    refetchInterval: 5000,
  });
}

export function useCreateFlow() {
  const qc = useQueryClient();
  return useMutation<FlowRead, unknown, FlowCreateInput>({
    mutationFn: (input) => api.post<FlowRead>("/api/v1/flows", input),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["flows"] }),
  });
}

export function useUpdateFlow(id: string) {
  const qc = useQueryClient();
  return useMutation<FlowRead, unknown, FlowUpdateInput>({
    mutationFn: (input) => api.patch<FlowRead>(`/api/v1/flows/${id}`, input),
    onSuccess: (updated) => {
      qc.setQueryData(["flow", id], updated);
      qc.invalidateQueries({ queryKey: ["flows"] });
    },
  });
}

export function useDeleteFlow() {
  const qc = useQueryClient();
  return useMutation<void, unknown, string>({
    mutationFn: (id) => api.delete(`/api/v1/flows/${id}`),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["flows"] }),
  });
}

export function useTriggerFlow(id: string) {
  const qc = useQueryClient();
  return useMutation<FlowRunRead, unknown, Record<string, unknown> | undefined>({
    mutationFn: (payload) =>
      api.post<FlowRunRead>(`/api/v1/flows/${id}/run`, {
        payload: payload ?? {},
      }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["flow-runs", id] });
      qc.invalidateQueries({ queryKey: ["flows"] });
    },
  });
}
