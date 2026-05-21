"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { ApiError, api } from "@/lib/api";
import { useAuthStore } from "@/stores/auth-store";
import { useWorkspaceStore } from "@/stores/workspace-store";

export interface AgentProfileToolset {
  name: string;
  use_count: number;
  effectiveness_avg: number | null;
}

export interface AgentProfileSkillCategory {
  category: string;
  use_count: number;
  success_rate?: number | null;
}

export interface AgentProfileDomain {
  domain: string;
  use_count: number;
  judge_avg: number | null;
}

export interface AgentProfileStrengths {
  toolsets?: AgentProfileToolset[];
  skill_categories?: AgentProfileSkillCategory[];
  domains?: AgentProfileDomain[];
  sample_artifact_count?: number;
}

export interface AgentProfileHallucinationKind {
  kind: string;
  count: number;
}

export interface AgentProfileCommonError {
  error_kind: string;
  count: number;
}

export interface AgentProfileErrorPattern {
  pattern_summary: string;
  frequency?: number;
  count?: number;
}

export interface AgentProfileFailureModes {
  hallucination_kinds?: AgentProfileHallucinationKind[];
  common_errors?: AgentProfileCommonError[];
  error_patterns?: AgentProfileErrorPattern[];
}

export interface AgentProfileRead {
  id: string;
  workspace_id: string;
  agent_id: string;
  strengths_json: AgentProfileStrengths;
  failure_modes_json: AgentProfileFailureModes;
  last_aggregated_at: string | null;
  aggregated_run_count: number;
  sample_size: number;
  created_at: string;
  updated_at: string;
}

export interface AgentProfileRefreshResult {
  workspace_id: string;
  agent_id: string;
  last_aggregated_at: string | null;
  aggregated_run_count: number;
  sample_size: number;
  strengths_json: AgentProfileStrengths;
  failure_modes_json: AgentProfileFailureModes;
  aux_skipped: boolean;
  aux_skip_reason: string | null;
}

export interface AgentProfileAdminRead extends AgentProfileRead {
  cross_workspace_stats_json: {
    total_runs_across_tenants?: number;
    median_judge_score?: number | null;
    top_failure_kinds?: AgentProfileCommonError[];
    workspace_count?: number;
    judged_run_count?: number;
  };
}

function profileKeys(workspaceId: string | null) {
  return {
    all: ["agent-profile", workspaceId] as const,
    detail: (agentId: string) =>
      ["agent-profile", workspaceId, agentId] as const,
  };
}

export function useAgentProfile(agentId: string | null | undefined) {
  const tok = useAuthStore((s) => s.accessToken);
  const ws = useWorkspaceStore((s) => s.activeWorkspaceId);
  return useQuery<AgentProfileRead | null>({
    queryKey: profileKeys(ws).detail(agentId ?? ""),
    queryFn: async () => {
      try {
        return await api.get<AgentProfileRead>(
          `/api/v1/agents/${agentId}/profile`,
        );
      } catch (err) {
        if (err instanceof ApiError && err.status === 404) {
          return null;
        }
        throw err;
      }
    },
    enabled: Boolean(tok && ws && agentId),
  });
}

export function useRefreshAgentProfile() {
  const qc = useQueryClient();
  const ws = useWorkspaceStore((s) => s.activeWorkspaceId);
  return useMutation<AgentProfileRefreshResult, unknown, { agentId: string }>({
    mutationFn: ({ agentId }) =>
      api.post<AgentProfileRefreshResult>(
        `/api/v1/agents/${agentId}/profile/refresh`,
      ),
    onSuccess: (_data, vars) => {
      qc.invalidateQueries({ queryKey: profileKeys(ws).detail(vars.agentId) });
      qc.invalidateQueries({ queryKey: profileKeys(ws).all });
    },
  });
}

export function useAgentProfileCrossWorkspace(
  agentId: string | null | undefined,
) {
  const tok = useAuthStore((s) => s.accessToken);
  return useQuery<AgentProfileAdminRead>({
    queryKey: ["agent-profile-admin", agentId] as const,
    queryFn: () =>
      api.get<AgentProfileAdminRead>(
        `/api/v1/admin/agents/${agentId}/profile/cross-workspace`,
      ),
    enabled: Boolean(tok && agentId),
  });
}
