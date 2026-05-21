"use client";

import { useQuery } from "@tanstack/react-query";

import { api } from "@/lib/api";
import { useAuthStore } from "@/stores/auth-store";
import { useWorkspaceStore } from "@/stores/workspace-store";

export interface AuxConfigBreaker {
  open: boolean;
  fail_strikes: number;
  fail_window_seconds: number;
  recover_seconds: number;
}

export interface AuxConfigRate {
  limit: number;
  used: number;
  period_seconds: number;
}

export interface AuxConfigRead {
  workspace_id: string;
  aux_model_default: string | null;
  aux_model_judge: string | null;
  aux_model_goal_alignment: string | null;
  judge_breaker: AuxConfigBreaker;
  judge_rate: AuxConfigRate;
}

export function useAuxConfig() {
  const token = useAuthStore((s) => s.accessToken);
  const ws = useWorkspaceStore((s) => s.activeWorkspaceId);
  const enabled = Boolean(token && ws);
  return useQuery<AuxConfigRead>({
    queryKey: ["aux-config", ws],
    queryFn: () =>
      api.get<AuxConfigRead>(`/api/v1/workspaces/${ws}/aux-config`),
    enabled,
    staleTime: 15_000,
    refetchInterval: 30_000,
  });
}
