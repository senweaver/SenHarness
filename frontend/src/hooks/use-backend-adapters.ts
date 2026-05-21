"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api } from "@/lib/api";

export interface BackendAdapterRead {
  id: string;
  workspace_id: string;
  name: string;
  kind: "openclaw";
  endpoint: string | null;
  capabilities_json: Record<string, unknown>;
  health_status: "unknown" | "healthy" | "degraded" | "down";
  last_seen_at: string | null;
  enabled: boolean;
  metadata_json: Record<string, unknown>;
  created_by: string | null;
  created_at: string;
  updated_at: string;
}

export interface BackendAdapterCreateInput {
  name: string;
  kind?: "openclaw";
  endpoint?: string | null;
  metadata_json?: Record<string, unknown>;
}

export interface BackendAdapterCreated {
  adapter: BackendAdapterRead;
  api_key: string;
}

export interface BackendAdapterHealthReport {
  status: "unknown" | "healthy" | "degraded" | "down";
  detail: string | null;
}

const KEY = ["backend-adapters"] as const;

export function useBackendAdapters() {
  return useQuery<BackendAdapterRead[]>({
    queryKey: KEY,
    queryFn: () => api.get<BackendAdapterRead[]>("/api/v1/backends"),
  });
}

export function useCreateBackendAdapter() {
  const qc = useQueryClient();
  return useMutation<BackendAdapterCreated, unknown, BackendAdapterCreateInput>({
    mutationFn: (input) =>
      api.post<BackendAdapterCreated>("/api/v1/backends", input),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: KEY });
    },
  });
}

export function useUpdateBackendAdapter(adapterId: string) {
  const qc = useQueryClient();
  return useMutation<
    BackendAdapterRead,
    unknown,
    {
      name?: string;
      endpoint?: string | null;
      enabled?: boolean;
      metadata_json?: Record<string, unknown>;
    }
  >({
    mutationFn: (input) =>
      api.patch<BackendAdapterRead>(`/api/v1/backends/${adapterId}`, input),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: KEY });
    },
  });
}

export function useRotateBackendAdapterKey(adapterId: string) {
  const qc = useQueryClient();
  return useMutation<BackendAdapterCreated, unknown, void>({
    mutationFn: () =>
      api.post<BackendAdapterCreated>(
        `/api/v1/backends/${adapterId}/rotate-key`,
        {},
      ),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: KEY });
    },
  });
}

export function usePingBackendAdapter(adapterId: string) {
  const qc = useQueryClient();
  return useMutation<BackendAdapterHealthReport, unknown, void>({
    mutationFn: () =>
      api.post<BackendAdapterHealthReport>(
        `/api/v1/backends/${adapterId}/health`,
        {},
      ),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: KEY });
    },
  });
}

export function useDeleteBackendAdapter() {
  const qc = useQueryClient();
  return useMutation<void, unknown, string>({
    mutationFn: (adapterId) => api.delete(`/api/v1/backends/${adapterId}`),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: KEY });
    },
  });
}
