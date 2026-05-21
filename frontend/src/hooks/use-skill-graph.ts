"use client";

import { useQuery } from "@tanstack/react-query";

import { ApiError, api } from "@/lib/api";
import { useAuthStore } from "@/stores/auth-store";
import { useWorkspaceStore } from "@/stores/workspace-store";
import type { SkillGraphRead, SkillLineageRead } from "@/types/api";

const GRAPH_MAX_DEPTH = 3;

function clampDepth(depth: number): number {
  if (Number.isNaN(depth)) return 2;
  return Math.max(1, Math.min(GRAPH_MAX_DEPTH, Math.trunc(depth)));
}

function graphKey(workspaceId: string | null, packId: string, depth: number) {
  return ["skill-graph", workspaceId, packId, depth] as const;
}

function lineageKey(workspaceId: string | null, packId: string) {
  return ["skill-lineage", workspaceId, packId] as const;
}

export function useSkillGraph(packId: string | null | undefined, depth = 2) {
  const tok = useAuthStore((s) => s.accessToken);
  const ws = useWorkspaceStore((s) => s.activeWorkspaceId);
  const bounded = clampDepth(depth);
  return useQuery<SkillGraphRead, ApiError>({
    queryKey: graphKey(ws, packId ?? "", bounded),
    queryFn: () =>
      api.get<SkillGraphRead>(
        `/api/v1/skills/packs/${packId}/graph?depth=${bounded}`,
      ),
    enabled: Boolean(tok && ws && packId),
  });
}

export function useSkillLineage(packId: string | null | undefined) {
  const tok = useAuthStore((s) => s.accessToken);
  const ws = useWorkspaceStore((s) => s.activeWorkspaceId);
  return useQuery<SkillLineageRead, ApiError>({
    queryKey: lineageKey(ws, packId ?? ""),
    queryFn: () =>
      api.get<SkillLineageRead>(`/api/v1/skills/packs/${packId}/lineage`),
    enabled: Boolean(tok && ws && packId),
  });
}

export const SKILL_GRAPH_MAX_DEPTH = GRAPH_MAX_DEPTH;
