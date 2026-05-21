"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "@/lib/api";
import { useAuthStore } from "@/stores/auth-store";

export interface GlobalStats {
  identities_total: number;
  identities_active: number;
  identities_suspended: number;
  platform_admins: number;
  workspaces_total: number;
  workspaces_active: number;
  sessions_total: number;
  messages_total: number;
  agents_total: number;
  flows_total: number;
  channels_total: number;
  audit_events_24h: number;
  new_identities_7d: number;
  new_workspaces_7d: number;
}

export type IdentityStatus = "pending" | "active" | "suspended";
export type PlatformRoleT = "user" | "platform_admin";

export interface IdentityAdminRow {
  id: string;
  email: string;
  name: string;
  avatar_url: string | null;
  status: IdentityStatus;
  platform_role: PlatformRoleT;
  oauth_provider: string | null;
  workspace_count: number;
  created_at: string;
  updated_at: string;
}

export interface WorkspaceBrief {
  id: string;
  name: string;
  slug: string;
  role: string;
}

export interface IdentityAdminDetail extends IdentityAdminRow {
  workspaces: WorkspaceBrief[];
}

export type WorkspacePlan = "free" | "team" | "business" | "enterprise";

export interface WorkspaceAdminRow {
  id: string;
  name: string;
  slug: string;
  description: string | null;
  plan: WorkspacePlan;
  member_count: number;
  agent_count: number;
  session_count: number;
  created_at: string;
  updated_at: string;
}

export interface WorkspaceAdminDetail extends WorkspaceAdminRow {
  branding_json: Record<string, unknown>;
  home_config_json: Record<string, unknown>;
  quota_json: Record<string, unknown>;
}

// ─── Stats ───────────────────────────────────────────────
export function useAdminStats() {
  const token = useAuthStore((s) => s.accessToken);
  return useQuery<GlobalStats>({
    queryKey: ["admin", "stats"],
    queryFn: () => api.get<GlobalStats>("/api/v1/admin/stats"),
    enabled: Boolean(token),
    refetchInterval: 60 * 1000,
  });
}

// ─── Identities ──────────────────────────────────────────
export interface IdentityQuery {
  q?: string;
  status?: IdentityStatus | "";
  role?: PlatformRoleT | "";
}

export function useAdminIdentities(params: IdentityQuery = {}) {
  const token = useAuthStore((s) => s.accessToken);
  const qs = new URLSearchParams();
  if (params.q) qs.set("q", params.q);
  if (params.status) qs.set("status", params.status);
  if (params.role) qs.set("role", params.role);
  return useQuery<IdentityAdminRow[]>({
    queryKey: ["admin", "identities", qs.toString()],
    queryFn: () =>
      api.get<IdentityAdminRow[]>(
        `/api/v1/admin/identities${qs.toString() ? "?" + qs.toString() : ""}`,
      ),
    enabled: Boolean(token),
  });
}

export function useAdminIdentity(id: string | null | undefined) {
  return useQuery<IdentityAdminDetail>({
    queryKey: ["admin", "identity", id],
    queryFn: () =>
      api.get<IdentityAdminDetail>(`/api/v1/admin/identities/${id}`),
    enabled: Boolean(id),
  });
}

export function usePatchIdentity() {
  const qc = useQueryClient();
  return useMutation<
    IdentityAdminDetail,
    unknown,
    {
      id: string;
      status?: IdentityStatus;
      platform_role?: PlatformRoleT;
    }
  >({
    mutationFn: ({ id, ...patch }) =>
      api.patch<IdentityAdminDetail>(`/api/v1/admin/identities/${id}`, patch),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["admin", "identities"] });
      qc.invalidateQueries({ queryKey: ["admin", "identity"] });
      qc.invalidateQueries({ queryKey: ["admin", "stats"] });
    },
  });
}

// ─── Workspaces ──────────────────────────────────────────
export interface WorkspaceQuery {
  q?: string;
  plan?: WorkspacePlan | "";
}

export function useAdminWorkspaces(params: WorkspaceQuery = {}) {
  const token = useAuthStore((s) => s.accessToken);
  const qs = new URLSearchParams();
  if (params.q) qs.set("q", params.q);
  if (params.plan) qs.set("plan", params.plan);
  return useQuery<WorkspaceAdminRow[]>({
    queryKey: ["admin", "workspaces", qs.toString()],
    queryFn: () =>
      api.get<WorkspaceAdminRow[]>(
        `/api/v1/admin/workspaces${qs.toString() ? "?" + qs.toString() : ""}`,
      ),
    enabled: Boolean(token),
  });
}

export function useAdminWorkspace(id: string | null | undefined) {
  return useQuery<WorkspaceAdminDetail>({
    queryKey: ["admin", "workspace", id],
    queryFn: () =>
      api.get<WorkspaceAdminDetail>(`/api/v1/admin/workspaces/${id}`),
    enabled: Boolean(id),
  });
}

export function usePatchWorkspace() {
  const qc = useQueryClient();
  return useMutation<
    WorkspaceAdminDetail,
    unknown,
    {
      id: string;
      name?: string;
      description?: string | null;
      plan?: WorkspacePlan;
    }
  >({
    mutationFn: ({ id, ...patch }) =>
      api.patch<WorkspaceAdminDetail>(`/api/v1/admin/workspaces/${id}`, patch),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["admin", "workspaces"] });
      qc.invalidateQueries({ queryKey: ["admin", "workspace"] });
    },
  });
}

export function useDeleteWorkspace() {
  const qc = useQueryClient();
  return useMutation<void, unknown, string>({
    mutationFn: (id) => api.delete(`/api/v1/admin/workspaces/${id}`),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["admin", "workspaces"] });
      qc.invalidateQueries({ queryKey: ["admin", "stats"] });
    },
  });
}

// ─── Cross-workspace approvals ──────────────────────────
export type AdminApprovalStatusFilter =
  | "all"
  | "pending"
  | "approved"
  | "denied"
  | "expired"
  | "cancelled";

export interface AdminApprovalRow {
  id: string;
  workspace_id: string;
  session_id: string;
  agent_id: string | null;
  run_id: string | null;
  tool_name: string;
  tool_args: Record<string, unknown>;
  summary: string | null;
  status: "pending" | "approved" | "denied" | "expired" | "cancelled";
  requested_by_identity_id: string | null;
  decided_by_identity_id: string | null;
  decided_reason: string | null;
  decided_at: string | null;
  expires_at: string | null;
  created_at: string;
  requester_department_name?: string | null;
  decided_by_department_name?: string | null;
  workspace_name: string | null;
  workspace_slug: string | null;
  requester_name: string | null;
  requester_email: string | null;
}

/**
 * `useAdminApprovals` — cross-tenant approvals list (platform admin only).
 *
 * Defaults to `status=pending` so the UI has a live triage queue; pass an
 * empty string (or a specific status) for the audit view.
 */
export function useAdminApprovals(params: {
  status?: AdminApprovalStatusFilter;
  workspaceId?: string | null;
  limit?: number;
}) {
  const token = useAuthStore((s) => s.accessToken);
  const qs = new URLSearchParams();
  if (params.status && params.status !== "all") qs.set("status", params.status);
  if (params.workspaceId) qs.set("workspace_id", params.workspaceId);
  if (params.limit) qs.set("limit", String(params.limit));
  return useQuery<AdminApprovalRow[]>({
    queryKey: ["admin", "approvals", qs.toString()],
    queryFn: () =>
      api.get<AdminApprovalRow[]>(
        `/api/v1/admin/approvals${qs.toString() ? "?" + qs.toString() : ""}`,
      ),
    enabled: Boolean(token),
    refetchInterval: 15_000,
  });
}

export function useAdminDecideApproval() {
  const qc = useQueryClient();
  return useMutation<
    AdminApprovalRow,
    unknown,
    { approvalId: string; action: "approve" | "deny"; reason?: string | null }
  >({
    mutationFn: ({ approvalId, action, reason }) =>
      api.post<AdminApprovalRow>(
        `/api/v1/admin/approvals/${approvalId}/decision`,
        { action, reason: reason ?? null },
      ),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["admin", "approvals"] });
      qc.invalidateQueries({ queryKey: ["approvals"] });
    },
  });
}
