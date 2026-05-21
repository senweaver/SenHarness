"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api } from "@/lib/api";

export type McpTransport = "stdio" | "sse" | "streamable_http";

export interface McpOAuthConfigInput {
  client_id: string;
  client_secret_ref?: string | null;
  client_secret?: string | null;
  token_url: string;
  scopes: string[];
  refresh_grace_seconds?: number;
}

export interface McpServerRead {
  id: string;
  workspace_id: string;
  name: string;
  slug: string;
  transport: string;
  endpoint: string | null;
  url: string | null;
  command: string | null;
  args_json: unknown[];
  env_json: Record<string, unknown>;
  auth_json: Record<string, unknown>;
  capabilities_json: Record<string, unknown>;
  health_status: "unknown" | "healthy" | "degraded" | "down";
  last_checked_at: string | null;
  enabled: boolean;
  metadata_json: Record<string, unknown>;
  created_by: string | null;
  created_at: string;
  updated_at: string;
}

export interface McpServerCreateInput {
  name: string;
  slug: string;
  transport: McpTransport;
  endpoint?: string | null;
  url?: string | null;
  command?: string | null;
  args_json?: unknown[];
  env_json?: Record<string, unknown>;
  auth_json?: Record<string, unknown>;
  auth_oauth?: McpOAuthConfigInput | null;
  capabilities_json?: Record<string, unknown>;
  enabled?: boolean;
  metadata_json?: Record<string, unknown>;
}

export interface McpToolCatalogueEntry {
  name: string;
  description: string | null;
  input_schema: Record<string, unknown>;
}

const KEY = ["mcp-servers"] as const;

export function useMcpServers() {
  return useQuery<McpServerRead[]>({
    queryKey: KEY,
    queryFn: () => api.get<McpServerRead[]>("/api/v1/mcp/servers"),
  });
}

export function useCreateMcpServer() {
  const qc = useQueryClient();
  return useMutation<McpServerRead, unknown, McpServerCreateInput>({
    mutationFn: (input) => api.post<McpServerRead>("/api/v1/mcp/servers", input),
    onSuccess: () => qc.invalidateQueries({ queryKey: KEY }),
  });
}

export function useUpdateMcpServer(serverId: string) {
  const qc = useQueryClient();
  return useMutation<McpServerRead, unknown, Partial<McpServerCreateInput>>({
    mutationFn: (patch) =>
      api.patch<McpServerRead>(`/api/v1/mcp/servers/${serverId}`, patch),
    onSuccess: () => qc.invalidateQueries({ queryKey: KEY }),
  });
}

export function useDeleteMcpServer() {
  const qc = useQueryClient();
  return useMutation<void, unknown, string>({
    mutationFn: (serverId) => api.delete(`/api/v1/mcp/servers/${serverId}`),
    onSuccess: () => qc.invalidateQueries({ queryKey: KEY }),
  });
}

export function usePingMcpServer(serverId: string) {
  const qc = useQueryClient();
  return useMutation<{ status: string; detail: string }, unknown, void>({
    mutationFn: () =>
      api.post<{ status: string; detail: string }>(
        `/api/v1/mcp/servers/${serverId}/health`,
        {},
      ),
    onSuccess: () => qc.invalidateQueries({ queryKey: KEY }),
  });
}

export function useListMcpServerTools(serverId: string) {
  return useMutation<McpToolCatalogueEntry[], unknown, void>({
    mutationFn: () =>
      api.post<McpToolCatalogueEntry[]>(
        `/api/v1/mcp/servers/${serverId}/tools`,
        {},
      ),
  });
}
