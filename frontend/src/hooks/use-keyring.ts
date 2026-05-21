"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api } from "@/lib/api";

export interface KeyringStatus {
  provider: string;
  current_kek_version: string;
  vault_items_total: number;
  vault_items_on_current_kek: number;
  rotation_supported: boolean;
}

export interface KeyringRotateResult {
  previous_version: string;
  new_version: string;
  rewrapped_count: number;
  skipped_count: number;
  duration_ms: number;
}

const KEY = ["keyring", "status"] as const;

export function useKeyringStatus() {
  return useQuery<KeyringStatus>({
    queryKey: KEY,
    queryFn: () => api.get<KeyringStatus>("/api/v1/keyring/status"),
  });
}

export function useRotateKek() {
  const qc = useQueryClient();
  return useMutation<KeyringRotateResult, unknown, void>({
    mutationFn: () =>
      api.post<KeyringRotateResult>("/api/v1/keyring/rotate", {}),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: KEY });
    },
  });
}
