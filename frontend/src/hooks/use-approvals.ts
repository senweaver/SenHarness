"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "@/lib/api";
import { useAuthStore } from "@/stores/auth-store";
import { useWorkspaceStore } from "@/stores/workspace-store";
import type { ApprovalRead } from "@/types/api";

interface PagedApprovals {
  items: ApprovalRead[];
  total: number;
  limit: number;
  offset: number;
}

export function usePendingApprovals(opts?: { sessionId?: string; enabled?: boolean }) {
  const tok = useAuthStore((s) => s.accessToken);
  const ws = useWorkspaceStore((s) => s.activeWorkspaceId);
  const params = new URLSearchParams({ status: "pending", limit: "50" });
  if (opts?.sessionId) params.set("session_id", opts.sessionId);
  return useQuery<ApprovalRead[]>({
    queryKey: ["approvals", "pending", ws, opts?.sessionId ?? null],
    queryFn: async () => {
      const res = await api.get<PagedApprovals>(`/api/v1/approvals?${params.toString()}`);
      return res.items;
    },
    enabled: Boolean(tok && ws) && (opts?.enabled ?? true),
    refetchInterval: 15_000,
  });
}

export function useRecentApprovals() {
  const tok = useAuthStore((s) => s.accessToken);
  const ws = useWorkspaceStore((s) => s.activeWorkspaceId);
  return useQuery<ApprovalRead[]>({
    queryKey: ["approvals", "recent", ws],
    queryFn: async () => {
      const res = await api.get<PagedApprovals>(`/api/v1/approvals?limit=100`);
      return res.items;
    },
    enabled: Boolean(tok && ws),
  });
}

export function useDecideApproval() {
  const qc = useQueryClient();
  return useMutation<
    ApprovalRead,
    unknown,
    { approvalId: string; action: "approve" | "deny"; reason?: string | null }
  >({
    mutationFn: ({ approvalId, action, reason }) =>
      api.post<ApprovalRead>(`/api/v1/approvals/${approvalId}/decision`, {
        action,
        reason: reason ?? null,
      }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["approvals"] });
    },
  });
}

export interface BulkDecisionItem {
  approval_id: string;
  ok: boolean;
  error_code: string | null;
  error_message: string | null;
}

export interface BulkDecisionResult {
  succeeded: string[];
  failed: BulkDecisionItem[];
}

/**
 * `useBulkDecideApprovals` — decide many approvals in one POST.
 *
 * The endpoint never aborts on the first failing row, so the returned
 * ``BulkDecisionResult`` always carries both succeeded ids and per-row
 * failures (with ``error_code`` ∈ {not_found, already_decided, no_permission,
 * internal}). The caller surfaces the failures in a summary dialog.
 */
export function useBulkDecideApprovals() {
  const qc = useQueryClient();
  return useMutation<
    BulkDecisionResult,
    unknown,
    { approvalIds: string[]; action: "approve" | "deny"; reason?: string | null }
  >({
    mutationFn: ({ approvalIds, action, reason }) =>
      api.post<BulkDecisionResult>(`/api/v1/approvals/bulk-decision`, {
        approval_ids: approvalIds,
        action,
        reason: reason ?? null,
      }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["approvals"] });
    },
  });
}

/**
 * `useUrgentApprovals` — top-N pending approvals sorted by urgency.
 *
 * Powers the sidebar bell preview: earliest-expiring first. Short poll so
 * the list stays roughly fresh; the WS push still drives primary updates.
 */
export function useUrgentApprovals(opts?: { limit?: number; enabled?: boolean }) {
  const tok = useAuthStore((s) => s.accessToken);
  const ws = useWorkspaceStore((s) => s.activeWorkspaceId);
  const limit = opts?.limit ?? 5;
  return useQuery<ApprovalRead[]>({
    queryKey: ["approvals", "urgent", ws, limit],
    queryFn: () =>
      api.get<ApprovalRead[]>(`/api/v1/approvals/urgent?limit=${limit}`),
    enabled: Boolean(tok && ws) && (opts?.enabled ?? true),
    refetchInterval: 30_000,
    staleTime: 10_000,
  });
}

/**
 * `useApprovalsCount` – lightweight poll for the pending-approvals badge
 * shown in the AvatarMenu and SiderNav. Short interval is fine because the
 * endpoint is a single `COUNT(*)` for workspaces with `approvals.view_all`,
 * and even the per-row visibility path is capped at 500 rows. WebSocket
 * pushes remain the primary notification channel; this poll is a redundancy
 * so a badge eventually clears even without an active chat socket.
 */
export function useApprovalsCount(opts?: { enabled?: boolean }) {
  const tok = useAuthStore((s) => s.accessToken);
  const ws = useWorkspaceStore((s) => s.activeWorkspaceId);
  return useQuery<{ pending: number }>({
    queryKey: ["approvals", "counts", ws],
    queryFn: () => api.get<{ pending: number }>(`/api/v1/approvals/counts`),
    enabled: Boolean(tok && ws) && (opts?.enabled ?? true),
    refetchInterval: 30_000,
    refetchOnWindowFocus: true,
    staleTime: 10_000,
  });
}
