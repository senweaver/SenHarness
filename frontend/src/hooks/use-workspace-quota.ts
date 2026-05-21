"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api } from "@/lib/api";
import { useAuthStore } from "@/stores/auth-store";
import type {
  AdminWorkspaceQuotaList,
  AdminWorkspaceQuotaRow,
  IdentityWorkspaceQuotaUpdate,
  WorkspaceQuota,
} from "@/types/api";

/** GET /api/v1/me/workspace-quota — current identity's effective budget. */
export function useWorkspaceQuota() {
  const token = useAuthStore((s) => s.accessToken);
  return useQuery<WorkspaceQuota>({
    queryKey: ["workspace-quota", "me"],
    queryFn: () => api.get<WorkspaceQuota>("/api/v1/me/workspace-quota"),
    enabled: Boolean(token),
    staleTime: 60_000,
  });
}

/** GET /api/v1/admin/workspace-quotas — paginated admin row list. */
export function useAdminQuotaList(params: {
  limit?: number;
  offset?: number;
  sortByUsage?: boolean;
}) {
  const token = useAuthStore((s) => s.accessToken);
  const limit = params.limit ?? 100;
  const offset = params.offset ?? 0;
  const sortByUsage = params.sortByUsage ?? true;
  const query = new URLSearchParams({
    limit: String(limit),
    offset: String(offset),
    sort_by_usage: sortByUsage ? "true" : "false",
  });
  return useQuery<AdminWorkspaceQuotaList>({
    queryKey: ["admin", "workspace-quotas", { limit, offset, sortByUsage }],
    queryFn: () =>
      api.get<AdminWorkspaceQuotaList>(
        `/api/v1/admin/workspace-quotas?${query.toString()}`,
      ),
    enabled: Boolean(token),
  });
}

/** GET /api/v1/admin/workspace-quotas/{identity_id}. */
export function useAdminIdentityQuota(identityId: string | null) {
  const token = useAuthStore((s) => s.accessToken);
  return useQuery<AdminWorkspaceQuotaRow>({
    queryKey: ["admin", "workspace-quotas", "identity", identityId],
    queryFn: () =>
      api.get<AdminWorkspaceQuotaRow>(
        `/api/v1/admin/workspace-quotas/${identityId}`,
      ),
    enabled: Boolean(token) && Boolean(identityId),
  });
}

/** PATCH /api/v1/admin/identities/{id}/workspace-quota — set / clear override. */
export function useUpdateIdentityQuota() {
  const qc = useQueryClient();
  return useMutation<
    IdentityWorkspaceQuotaUpdate,
    unknown,
    { identityId: string; quota: number | null }
  >({
    mutationFn: ({ identityId, quota }) =>
      api.patch<IdentityWorkspaceQuotaUpdate>(
        `/api/v1/admin/identities/${identityId}/workspace-quota`,
        { quota },
      ),
    onSuccess: (_data, vars) => {
      qc.invalidateQueries({ queryKey: ["admin", "workspace-quotas"] });
      qc.invalidateQueries({
        queryKey: ["admin", "workspace-quotas", "identity", vars.identityId],
      });
      qc.invalidateQueries({ queryKey: ["workspace-quota", "me"] });
    },
  });
}
