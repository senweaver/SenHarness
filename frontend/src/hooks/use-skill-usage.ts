"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "@/lib/api";
import { useAuthStore } from "@/stores/auth-store";
import { useWorkspaceStore } from "@/stores/workspace-store";

export type SkillUsageEventKind =
  | "injected"
  | "read_full"
  | "used_in_tool"
  | "patched"
  | "dropped_at_cap";

export interface SkillUsageRow {
  id: string;
  workspace_id: string;
  pack_id: string;
  version_id: string | null;
  run_id: string;
  session_id: string;
  agent_id: string | null;
  identity_id: string | null;
  event_kind: SkillUsageEventKind;
  contribution_score: number | null;
  created_at: string;
  updated_at: string;
}

export interface SkillUsageList {
  pack_id: string;
  items: SkillUsageRow[];
}

export interface SkillUsageStats {
  pack_id: string;
  window_days: number;
  use_count: number;
  last_used_at: string | null;
  contribution_avg: number | null;
  use_count_by_kind: Partial<Record<SkillUsageEventKind, number>>;
  trend_7d: Partial<Record<SkillUsageEventKind, number>>;
}

export interface SkillUsageRollupResult {
  pack_id: string;
  last_used_at: string | null;
  effectiveness_avg: number | null;
  use_count: number;
  rolled_up_at: string;
}

function usageKeys(workspaceId: string | null) {
  return {
    all: ["skill-usage", workspaceId] as const,
    list: (packId: string, eventKind?: SkillUsageEventKind | "all") =>
      ["skill-usage", workspaceId, packId, "list", eventKind ?? "all"] as const,
    stats: (packId: string) =>
      ["skill-usage", workspaceId, packId, "stats"] as const,
  };
}

interface UseSkillUsageOptions {
  limit?: number;
  eventKind?: SkillUsageEventKind | "all";
}

export function useSkillUsage(
  packId: string | null | undefined,
  opts: UseSkillUsageOptions = {},
) {
  const tok = useAuthStore((s) => s.accessToken);
  const ws = useWorkspaceStore((s) => s.activeWorkspaceId);
  const limit = opts.limit ?? 50;
  const kind = opts.eventKind ?? "all";

  return useQuery<SkillUsageList>({
    queryKey: usageKeys(ws).list(packId ?? "", kind),
    queryFn: () => {
      const qs = new URLSearchParams({ limit: String(limit) });
      if (kind !== "all") qs.set("event_kind", kind);
      return api.get<SkillUsageList>(
        `/api/v1/skills/packs/${packId}/usage?${qs.toString()}`,
      );
    },
    enabled: Boolean(tok && ws && packId),
  });
}

export function useSkillUsageStats(
  packId: string | null | undefined,
  windowDays = 30,
) {
  const tok = useAuthStore((s) => s.accessToken);
  const ws = useWorkspaceStore((s) => s.activeWorkspaceId);
  return useQuery<SkillUsageStats>({
    queryKey: [...usageKeys(ws).stats(packId ?? ""), windowDays] as const,
    queryFn: () =>
      api.get<SkillUsageStats>(
        `/api/v1/skills/packs/${packId}/usage/stats?window_days=${windowDays}`,
      ),
    enabled: Boolean(tok && ws && packId),
  });
}

export function useTriggerSkillRollup() {
  const qc = useQueryClient();
  const ws = useWorkspaceStore((s) => s.activeWorkspaceId);
  return useMutation<SkillUsageRollupResult, unknown, { packId: string }>({
    mutationFn: ({ packId }) =>
      api.post<SkillUsageRollupResult>(
        `/api/v1/skills/packs/${packId}/usage/rollup`,
      ),
    onSuccess: (_data, vars) => {
      qc.invalidateQueries({ queryKey: usageKeys(ws).all });
      qc.invalidateQueries({ queryKey: usageKeys(ws).stats(vars.packId) });
      qc.invalidateQueries({ queryKey: ["skill-packs", ws] });
    },
  });
}
