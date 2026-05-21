"use client";

/**
 * Governance hooks — policies, budgets, usage events, tool-call logs.
 *
 * Backend routes: see `backend/app/api/v1/governance.py`.
 *
 * Scope semantics (matches `GovernanceScope` enum):
 *   - ``global`` — platform-wide policy/budget, requires platform_admin.
 *   - ``workspace`` — tenant-scoped, requires workspace admin.
 *   - ``agent`` — narrowest; workspace admin, tied to an agent.
 *
 * The list endpoints return the union of ``global`` + current-workspace rows.
 * Callers filter client-side when they want to split the two views
 * (``filterScopes=[global]`` on /admin/governance, etc.).
 *
 * Hook shape convention — kept symmetric with existing code in the repo:
 *
 *   - ``usePolicies()`` / ``useBudgets()`` — list + cache.
 *   - ``useCreatePolicy()`` / ``useCreateBudget()`` — mutation taking the
 *     full payload.
 *   - ``useUpdatePolicy(id)`` / ``useUpdateBudget(id)`` — mutation whose id
 *     is bound at hook call time; ``mutateAsync(patch)`` takes just the
 *     patch fields.
 *   - ``useDeletePolicy()`` / ``useDeleteBudget()`` — mutation taking the
 *     id at call time.
 *   - ``useUsageEvents({ limit })`` / ``useToolCallLogs({ limit })`` —
 *     read-only list, paginated params as an object for future filter
 *     expansion.
 */

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "@/lib/api";
import { useAuthStore } from "@/stores/auth-store";
import { useWorkspaceStore } from "@/stores/workspace-store";

export type GovernanceScope = "global" | "workspace" | "agent";
export type BudgetPeriod = "daily" | "weekly" | "monthly";

export interface PolicyRead {
    id: string;
    scope: GovernanceScope;
    workspace_id: string | null;
    agent_id: string | null;
    name: string;
    description: string | null;
    enabled: boolean;
    priority: number;
    rules_json: Record<string, unknown>;
    metadata_json: Record<string, unknown>;
    created_by: string | null;
    created_at: string;
    updated_at: string;
}

export interface PolicyCreate {
    name: string;
    description?: string | null;
    scope: GovernanceScope;
    workspace_id?: string | null;
    agent_id?: string | null;
    enabled?: boolean;
    priority?: number;
    rules_json?: Record<string, unknown>;
    metadata_json?: Record<string, unknown>;
}

export type PolicyUpdate = Partial<PolicyCreate>;

export interface BudgetRead {
    id: string;
    scope: GovernanceScope;
    workspace_id: string | null;
    agent_id: string | null;
    name: string;
    currency: string;
    period: BudgetPeriod;
    // `Decimal` arrives as a JSON string on the wire; parse defensively.
    limit_amount: string;
    alert_threshold_pct: number | null;
    enabled: boolean;
    metadata_json: Record<string, unknown>;
    created_by: string | null;
    created_at: string;
    updated_at: string;
}

export interface BudgetCreate {
    name: string;
    scope: GovernanceScope;
    workspace_id?: string | null;
    agent_id?: string | null;
    currency?: string;
    period?: BudgetPeriod;
    limit_amount: number | string;
    alert_threshold_pct?: number | null;
    enabled?: boolean;
    metadata_json?: Record<string, unknown>;
}

export type BudgetUpdate = Partial<BudgetCreate>;

export interface UsageEventRead {
    id: string;
    workspace_id: string;
    agent_id: string | null;
    session_id: string | null;
    policy_id: string | null;
    budget_id: string | null;
    event_type: string;
    provider: string | null;
    model: string | null;
    input_tokens: number | null;
    output_tokens: number | null;
    cost_usd: string | null;
    tool_name: string | null;
    metadata_json: Record<string, unknown>;
    created_at: string;
    updated_at: string;
}

export interface ToolCallLogRead {
    id: string;
    workspace_id: string;
    agent_id: string | null;
    session_id: string | null;
    policy_id: string | null;
    tool_name: string;
    status: string;
    duration_ms: number | null;
    input_json: Record<string, unknown>;
    output_json: Record<string, unknown>;
    error_text: string | null;
    cost_usd: string | null;
    metadata_json: Record<string, unknown>;
    created_at: string;
    updated_at: string;
}

// ─── Policies ──────────────────────────────────────────────
export function usePolicies() {
    const token = useAuthStore((s) => s.accessToken);
    const ws = useWorkspaceStore((s) => s.activeWorkspaceId);
    return useQuery<PolicyRead[]>({
        queryKey: ["governance", "policies", ws],
        queryFn: () =>
            api.get<PolicyRead[]>("/api/v1/governance/policies?limit=500"),
        enabled: Boolean(token && ws),
        staleTime: 30_000,
    });
}

export function useCreatePolicy() {
    const qc = useQueryClient();
    return useMutation<PolicyRead, unknown, PolicyCreate>({
        mutationFn: (body) =>
            api.post<PolicyRead>("/api/v1/governance/policies", body),
        onSuccess: () => {
            qc.invalidateQueries({ queryKey: ["governance", "policies"] });
        },
    });
}

/**
 * Edit an existing policy. ``id`` is bound at hook-construction time so
 * form components can wire ``useUpdatePolicy(policy.id)`` once and call
 * ``mutateAsync(patch)`` with just the changed fields.
 */
export function useUpdatePolicy(id: string) {
    const qc = useQueryClient();
    return useMutation<PolicyRead, unknown, PolicyUpdate>({
        mutationFn: (patch) =>
            api.patch<PolicyRead>(`/api/v1/governance/policies/${id}`, patch),
        onSuccess: () => {
            qc.invalidateQueries({ queryKey: ["governance", "policies"] });
        },
    });
}

export function useDeletePolicy() {
    const qc = useQueryClient();
    return useMutation<void, unknown, string>({
        mutationFn: (id) => api.delete(`/api/v1/governance/policies/${id}`),
        onSuccess: () => {
            qc.invalidateQueries({ queryKey: ["governance", "policies"] });
        },
    });
}

// ─── Budgets ──────────────────────────────────────────────
export function useBudgets() {
    const token = useAuthStore((s) => s.accessToken);
    const ws = useWorkspaceStore((s) => s.activeWorkspaceId);
    return useQuery<BudgetRead[]>({
        queryKey: ["governance", "budgets", ws],
        queryFn: () =>
            api.get<BudgetRead[]>("/api/v1/governance/budgets?limit=500"),
        enabled: Boolean(token && ws),
        staleTime: 30_000,
    });
}

export function useCreateBudget() {
    const qc = useQueryClient();
    return useMutation<BudgetRead, unknown, BudgetCreate>({
        mutationFn: (body) =>
            api.post<BudgetRead>("/api/v1/governance/budgets", body),
        onSuccess: () => {
            qc.invalidateQueries({ queryKey: ["governance", "budgets"] });
        },
    });
}

export function useUpdateBudget(id: string) {
    const qc = useQueryClient();
    return useMutation<BudgetRead, unknown, BudgetUpdate>({
        mutationFn: (patch) =>
            api.patch<BudgetRead>(`/api/v1/governance/budgets/${id}`, patch),
        onSuccess: () => {
            qc.invalidateQueries({ queryKey: ["governance", "budgets"] });
        },
    });
}

export function useDeleteBudget() {
    const qc = useQueryClient();
    return useMutation<void, unknown, string>({
        mutationFn: (id) => api.delete(`/api/v1/governance/budgets/${id}`),
        onSuccess: () => {
            qc.invalidateQueries({ queryKey: ["governance", "budgets"] });
        },
    });
}

// ─── Usage events (read-only in UI) ────────────────────────
export interface UsageEventsQuery {
    limit?: number;
    offset?: number;
}

export function useUsageEvents(params: UsageEventsQuery = {}) {
    const token = useAuthStore((s) => s.accessToken);
    const ws = useWorkspaceStore((s) => s.activeWorkspaceId);
    const limit = params.limit ?? 200;
    const offset = params.offset ?? 0;
    return useQuery<UsageEventRead[]>({
        queryKey: ["governance", "usage-events", ws, limit, offset],
        queryFn: () =>
            api.get<UsageEventRead[]>(
                `/api/v1/governance/usage-events?limit=${limit}&offset=${offset}`,
            ),
        enabled: Boolean(token && ws),
        staleTime: 15_000,
    });
}

// ─── Tool-call logs (read-only in UI) ──────────────────────
export interface ToolCallLogsQuery {
    limit?: number;
    offset?: number;
}

export function useToolCallLogs(params: ToolCallLogsQuery = {}) {
    const token = useAuthStore((s) => s.accessToken);
    const ws = useWorkspaceStore((s) => s.activeWorkspaceId);
    const limit = params.limit ?? 200;
    const offset = params.offset ?? 0;
    return useQuery<ToolCallLogRead[]>({
        queryKey: ["governance", "tool-call-logs", ws, limit, offset],
        queryFn: () =>
            api.get<ToolCallLogRead[]>(
                `/api/v1/governance/tool-call-logs?limit=${limit}&offset=${offset}`,
            ),
        enabled: Boolean(token && ws),
        staleTime: 15_000,
    });
}
