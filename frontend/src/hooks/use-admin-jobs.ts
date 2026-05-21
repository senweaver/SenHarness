"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api } from "@/lib/api";
import { useAuthStore } from "@/stores/auth-store";

export type JobRunStatus =
  | "queued"
  | "running"
  | "success"
  | "failed"
  | "failed_permanent";

export interface JobRunRow {
  id: string;
  job_id: string;
  function_name: string;
  workspace_id: string | null;
  identity_id: string | null;
  status: JobRunStatus;
  enqueued_at: string;
  started_at: string | null;
  finished_at: string | null;
  duration_ms: number | null;
  retry_count: number;
  args_json: Record<string, unknown>;
  error_class: string | null;
  error_message: string | null;
  created_at: string;
}

export interface JobFunctionStats {
  function_name: string;
  queued: number;
  running: number;
  success: number;
  failed: number;
  failed_permanent: number;
}

export interface QueueDepth {
  queue_name: string;
  depth: number | null;
  error: string | null;
}

export interface JobQueuesResponse {
  window_seconds: number;
  by_function: JobFunctionStats[];
  redis_queue: QueueDepth;
}

export interface JobHealthTotals {
  queued: number;
  running: number;
  success: number;
  failed: number;
  failed_permanent: number;
  failed_permanent_total: number;
}

export interface JobHealthResponse {
  window_started_at: string;
  window_seconds: number;
  totals: JobHealthTotals;
  by_function: Record<string, Record<string, number>>;
}

export interface RecentJobsParams {
  status?: JobRunStatus | "";
  function_name?: string;
  limit?: number;
}

function buildQuery(params: Record<string, string | number | undefined>): string {
  const qs = new URLSearchParams();
  Object.entries(params).forEach(([k, v]) => {
    if (v === undefined || v === null || v === "") return;
    qs.set(k, String(v));
  });
  return qs.toString();
}

export function useJobQueues(params: { window_seconds?: number } = {}) {
  const token = useAuthStore((s) => s.accessToken);
  const qs = buildQuery({ window_seconds: params.window_seconds });
  return useQuery<JobQueuesResponse>({
    queryKey: ["admin", "jobs", "queues", qs],
    queryFn: () =>
      api.get<JobQueuesResponse>(
        `/api/v1/admin/jobs/queues${qs ? "?" + qs : ""}`,
      ),
    enabled: Boolean(token),
    refetchInterval: 10_000,
  });
}

export function useJobHealth(params: { window_seconds?: number } = {}) {
  const token = useAuthStore((s) => s.accessToken);
  const qs = buildQuery({ window_seconds: params.window_seconds });
  return useQuery<JobHealthResponse>({
    queryKey: ["admin", "jobs", "health", qs],
    queryFn: () =>
      api.get<JobHealthResponse>(
        `/api/v1/admin/jobs/health${qs ? "?" + qs : ""}`,
      ),
    enabled: Boolean(token),
    refetchInterval: 10_000,
  });
}

export function useRecentJobs(params: RecentJobsParams = {}) {
  const token = useAuthStore((s) => s.accessToken);
  const qs = buildQuery({
    status: params.status,
    function_name: params.function_name,
    limit: params.limit ?? 200,
  });
  return useQuery<JobRunRow[]>({
    queryKey: ["admin", "jobs", "recent", qs],
    queryFn: () =>
      api.get<JobRunRow[]>(
        `/api/v1/admin/jobs/recent${qs ? "?" + qs : ""}`,
      ),
    enabled: Boolean(token),
    refetchInterval: 10_000,
  });
}

export interface RetryResponse {
  enqueued: boolean;
  new_job_id: string | null;
  function_name: string | null;
}

export function useRetryJob() {
  const qc = useQueryClient();
  return useMutation<RetryResponse, unknown, string>({
    mutationFn: (jobId) =>
      api.post<RetryResponse>(`/api/v1/admin/jobs/${jobId}/retry`, {}),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["admin", "jobs"] });
    },
  });
}
