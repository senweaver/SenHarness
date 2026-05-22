"use client";

/**
 * Workspace-level Skill Curator config hooks (M1.9).
 *
 * Backend: `backend/app/api/v1/workspaces.py` —
 * `/workspaces/{id}/settings/curator{,run-now,last-run}` (4 routes).
 *
 * The merged config is workspace override > platform default; the
 * `source` map per knob lets the UI render "workspace override" /
 * "platform default" badges so the admin can see at a glance which
 * knobs have been customised.
 */

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api } from "@/lib/api";
import { useAuthStore } from "@/stores/auth-store";
import { useWorkspaceStore } from "@/stores/workspace-store";

export type CuratorFieldSource = "workspace" | "platform_default";

export interface CuratorConfig {
  enabled: boolean;
  stale_after_days: number;
  archive_after_days: number;
  min_idle_hours: number;
  active_skills_soft_cap: number;
  source: Record<CuratorFieldName, CuratorFieldSource>;
}

export interface CuratorConfigPatch {
  enabled?: boolean | null;
  stale_after_days?: number | null;
  archive_after_days?: number | null;
  min_idle_hours?: number | null;
  active_skills_soft_cap?: number | null;
}

export interface CuratorRunResult {
  workspace_id: string;
  stale_proposed: number;
  archive_proposed: number;
  pinned_skipped: number;
  duration_ms: number;
  started_at: string;
  finished_at: string;
}

export interface CuratorLastRun {
  last_run_at: string | null;
  last_result: CuratorRunResult | null;
  upcoming_run_at: string | null;
}

export const CURATOR_FIELD_NAMES = [
  "enabled",
  "stale_after_days",
  "archive_after_days",
  "min_idle_hours",
  "active_skills_soft_cap",
] as const;
export type CuratorFieldName = (typeof CURATOR_FIELD_NAMES)[number];

export const CURATOR_FIELD_RANGES: Record<
  Exclude<CuratorFieldName, "enabled">,
  { min: number; max: number }
> = {
  stale_after_days: { min: 1, max: 365 },
  archive_after_days: { min: 1, max: 365 },
  min_idle_hours: { min: 0, max: 720 },
  active_skills_soft_cap: { min: 1, max: 1000 },
};

export function useCuratorConfig(workspaceId: string | null | undefined) {
  const tok = useAuthStore((s) => s.accessToken);
  const ws = useWorkspaceStore((s) => s.activeWorkspaceId);
  const target = workspaceId ?? ws;
  return useQuery<CuratorConfig>({
    queryKey: ["curator-config", target],
    queryFn: () =>
      api.get<CuratorConfig>(
        `/api/v1/workspaces/${target}/settings/curator`,
      ),
    enabled: Boolean(tok && target),
  });
}

export function useUpdateCuratorConfig(
  workspaceId: string | null | undefined,
) {
  const qc = useQueryClient();
  const ws = useWorkspaceStore((s) => s.activeWorkspaceId);
  const target = workspaceId ?? ws;
  return useMutation<CuratorConfig, unknown, CuratorConfigPatch>({
    mutationFn: (patch) =>
      api.patch<CuratorConfig>(
        `/api/v1/workspaces/${target}/settings/curator`,
        patch,
      ),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["curator-config", target] });
      qc.invalidateQueries({ queryKey: ["curator-last-run", target] });
    },
  });
}

export function useForceCuratorRun(workspaceId: string | null | undefined) {
  const qc = useQueryClient();
  const ws = useWorkspaceStore((s) => s.activeWorkspaceId);
  const target = workspaceId ?? ws;
  return useMutation<CuratorRunResult, unknown, void>({
    mutationFn: () =>
      api.post<CuratorRunResult>(
        `/api/v1/workspaces/${target}/settings/curator/run-now`,
      ),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["curator-last-run", target] });
    },
  });
}

export function useCuratorLastRun(workspaceId: string | null | undefined) {
  const tok = useAuthStore((s) => s.accessToken);
  const ws = useWorkspaceStore((s) => s.activeWorkspaceId);
  const target = workspaceId ?? ws;
  return useQuery<CuratorLastRun>({
    queryKey: ["curator-last-run", target],
    queryFn: () =>
      api.get<CuratorLastRun>(
        `/api/v1/workspaces/${target}/settings/curator/last-run`,
      ),
    enabled: Boolean(tok && target),
  });
}
