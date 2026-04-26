"use client";

import { useQuery } from "@tanstack/react-query";
import { api } from "@/lib/api";
import { useAuthStore } from "@/stores/auth-store";
import { useWorkspaceStore } from "@/stores/workspace-store";

export interface AuditEvent {
  id: string;
  workspace_id: string | null;
  actor_identity_id: string | null;
  action: string;
  resource_type: string | null;
  resource_id: string | null;
  summary: string | null;
  metadata_json: Record<string, unknown>;
  ip_address: string | null;
  user_agent: string | null;
  created_at: string;
  actor_name: string | null;
  actor_email: string | null;
}

export interface AuditQuery {
  scope?: "workspace" | "platform";
  since?: string;
  until?: string;
  action?: string;
  actor?: string;
  resource_type?: string;
  resource_id?: string;
  q?: string;
  limit?: number;
  offset?: number;
}

function toQueryString(p: AuditQuery): string {
  const qs = new URLSearchParams();
  Object.entries(p).forEach(([k, v]) => {
    if (v === undefined || v === null || v === "") return;
    qs.set(k, String(v));
  });
  return qs.toString();
}

export function useAuditEvents(params: AuditQuery = {}) {
  const token = useAuthStore((s) => s.accessToken);
  const ws = useWorkspaceStore((s) => s.activeWorkspaceId);
  const qs = toQueryString(params);
  return useQuery<AuditEvent[]>({
    queryKey: ["audit", "events", ws, qs],
    queryFn: () =>
      api.get<AuditEvent[]>(`/api/v1/audit/events${qs ? "?" + qs : ""}`),
    enabled: Boolean(token && ws),
    staleTime: 30 * 1000,
  });
}

export function buildAuditCsvUrl(
  apiBase: string,
  accessToken: string | null,
  workspaceId: string | null,
  params: AuditQuery,
): { url: string; headers: HeadersInit } {
  const qs = toQueryString({ ...params, limit: params.limit ?? 5000 });
  const url = `${apiBase}/api/v1/audit/events.csv${qs ? "?" + qs : ""}`;
  const headers: HeadersInit = {};
  if (accessToken) headers["Authorization"] = `Bearer ${accessToken}`;
  if (workspaceId) headers["X-Workspace-Id"] = workspaceId;
  return { url, headers };
}
