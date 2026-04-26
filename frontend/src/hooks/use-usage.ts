"use client";

import { useQuery } from "@tanstack/react-query";
import { api } from "@/lib/api";
import { useAuthStore } from "@/stores/auth-store";
import { useWorkspaceStore } from "@/stores/workspace-store";

export interface UsageSummary {
  input_tokens: number;
  output_tokens: number;
  cost_usd: number;
  turns: number;
  sessions: number;
  avg_latency_ms: number;
}

export interface UsageDailyBucket {
  date: string;
  input_tokens: number;
  output_tokens: number;
  cost_usd: number;
  turns: number;
}

export interface UsageByAgent {
  agent_id: string | null;
  agent_name: string | null;
  input_tokens: number;
  output_tokens: number;
  cost_usd: number;
  turns: number;
}

export interface UsageByModel {
  model: string;
  provider: string;
  input_tokens: number;
  output_tokens: number;
  cost_usd: number;
  turns: number;
}

export interface UsageReport {
  since: string;
  until: string;
  scope: "me" | "workspace";
  summary: UsageSummary;
  daily: UsageDailyBucket[];
  top_agents: UsageByAgent[];
  top_models: UsageByModel[];
}

export interface UsageQuery {
  since?: string;
  until?: string;
  scope?: "auto" | "me" | "workspace";
  top?: number;
}

export function useUsageReport(params: UsageQuery = {}) {
  const token = useAuthStore((s) => s.accessToken);
  const ws = useWorkspaceStore((s) => s.activeWorkspaceId);
  const qs = new URLSearchParams();
  if (params.since) qs.set("since", params.since);
  if (params.until) qs.set("until", params.until);
  if (params.scope && params.scope !== "auto") qs.set("scope", params.scope);
  if (params.top != null) qs.set("top", String(params.top));
  const query = qs.toString();

  return useQuery<UsageReport>({
    queryKey: ["usage", ws, query],
    queryFn: () =>
      api.get<UsageReport>(`/api/v1/metrics/usage${query ? "?" + query : ""}`),
    enabled: Boolean(token && ws),
    staleTime: 60 * 1000,
  });
}
