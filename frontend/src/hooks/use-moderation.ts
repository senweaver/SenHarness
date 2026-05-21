"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "@/lib/api";
import { useAuthStore } from "@/stores/auth-store";
import { useWorkspaceStore } from "@/stores/workspace-store";

export type ReportReason =
  | "spam"
  | "inappropriate"
  | "copyright"
  | "security"
  | "misinformation"
  | "other";

export type ReportStatus =
  | "pending"
  | "reviewed"
  | "dismissed"
  | "removed";

export interface AgentReport {
  id: string;
  agent_id: string;
  agent_name: string | null;
  agent_workspace_id: string | null;
  reporter_identity_id: string | null;
  reporter_name: string | null;
  reason: ReportReason;
  detail: string | null;
  status: ReportStatus;
  review_decision: string | null;
  reviewed_by_identity_id: string | null;
  reviewer_name: string | null;
  metadata_json: Record<string, unknown>;
  created_at: string;
  updated_at: string;
}

export function useReports(status?: ReportStatus) {
  const token = useAuthStore((s) => s.accessToken);
  const ws = useWorkspaceStore((s) => s.activeWorkspaceId);
  const qs = status ? `?status=${status}` : "";
  return useQuery<AgentReport[]>({
    queryKey: ["moderation", "reports", ws, status ?? "all"],
    queryFn: () =>
      api.get<AgentReport[]>(`/api/v1/moderation/reports${qs}`),
    enabled: Boolean(token && ws),
    staleTime: 30 * 1000,
  });
}

export function useDecideReport() {
  const qc = useQueryClient();
  return useMutation<
    AgentReport,
    unknown,
    { reportId: string; decision: ReportStatus; note?: string | null }
  >({
    mutationFn: ({ reportId, decision, note }) =>
      api.patch<AgentReport>(`/api/v1/moderation/reports/${reportId}`, {
        decision,
        note: note ?? null,
      }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["moderation", "reports"] });
      qc.invalidateQueries({ queryKey: ["marketplace"] });
    },
  });
}

export function useReportAgent() {
  return useMutation<
    AgentReport,
    unknown,
    { agentId: string; reason: ReportReason; detail?: string | null }
  >({
    mutationFn: ({ agentId, reason, detail }) =>
      api.post<AgentReport>(`/api/v1/agents/${agentId}/report`, {
        reason,
        detail: detail ?? null,
      }),
  });
}
