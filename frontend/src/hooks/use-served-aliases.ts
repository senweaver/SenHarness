"use client";

/**
 * Workspace-level served alias map hooks (M2.5.7).
 *
 * Backend: `backend/app/api/v1/workspaces.py` —
 * `/workspaces/{id}/settings/served-aliases{,/<served_name>}` (3 routes).
 *
 * The alias map decouples the client-facing `served_model_name`
 * from the upstream provider model id, so swapping a provider
 * does not invalidate provider-side prompt caches keyed on the
 * model name.
 */

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api } from "@/lib/api";
import { useAuthStore } from "@/stores/auth-store";
import { useWorkspaceStore } from "@/stores/workspace-store";

export interface ServedAlias {
  served_name: string;
  upstream: string;
}

export interface ServedAliasListResponse {
  aliases: ServedAlias[];
}

export function useServedAliases(workspaceId?: string | null) {
  const tok = useAuthStore((s) => s.accessToken);
  const ws = useWorkspaceStore((s) => s.activeWorkspaceId);
  const target = workspaceId ?? ws;
  return useQuery<ServedAliasListResponse>({
    queryKey: ["served-aliases", target],
    queryFn: () =>
      api.get<ServedAliasListResponse>(
        `/api/v1/workspaces/${target}/settings/served-aliases`,
      ),
    enabled: Boolean(tok && target),
  });
}

export function useUpsertServedAlias(workspaceId?: string | null) {
  const qc = useQueryClient();
  const ws = useWorkspaceStore((s) => s.activeWorkspaceId);
  const target = workspaceId ?? ws;
  return useMutation<ServedAlias, unknown, ServedAlias>({
    mutationFn: ({ served_name, upstream }) =>
      api.put<ServedAlias>(
        `/api/v1/workspaces/${target}/settings/served-aliases/${encodeURIComponent(
          served_name,
        )}`,
        { upstream },
      ),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["served-aliases", target] });
    },
  });
}

export function useDeleteServedAlias(workspaceId?: string | null) {
  const qc = useQueryClient();
  const ws = useWorkspaceStore((s) => s.activeWorkspaceId);
  const target = workspaceId ?? ws;
  return useMutation<void, unknown, { served_name: string }>({
    mutationFn: ({ served_name }) =>
      api.delete<void>(
        `/api/v1/workspaces/${target}/settings/served-aliases/${encodeURIComponent(
          served_name,
        )}`,
      ),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["served-aliases", target] });
    },
  });
}
