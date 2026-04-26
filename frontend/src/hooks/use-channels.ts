"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "@/lib/api";
import { useAuthStore } from "@/stores/auth-store";
import { useWorkspaceStore } from "@/stores/workspace-store";

export type ChannelKind = "slack" | "feishu" | "discord" | "webhook";

export interface ChannelRead {
  id: string;
  workspace_id: string;
  name: string;
  kind: ChannelKind;
  inbound_token: string;
  config_json: Record<string, unknown>;
  default_agent_id: string | null;
  default_squad_id: string | null;
  enabled: boolean;
  metadata_json: Record<string, unknown>;
  created_at: string;
  updated_at: string;
  created_by: string | null;
}

export interface ChannelCreateInput {
  name: string;
  kind: ChannelKind;
  config_json?: Record<string, unknown>;
  default_agent_id?: string | null;
  default_squad_id?: string | null;
  enabled?: boolean;
  metadata_json?: Record<string, unknown>;
}

export type ChannelUpdateInput = Partial<ChannelCreateInput>;

export function useChannels() {
  const token = useAuthStore((s) => s.accessToken);
  const ws = useWorkspaceStore((s) => s.activeWorkspaceId);
  return useQuery<ChannelRead[]>({
    queryKey: ["channels", ws],
    queryFn: () => api.get<ChannelRead[]>("/api/v1/channels"),
    enabled: Boolean(token && ws),
  });
}

export function useChannel(id: string | null | undefined) {
  return useQuery<ChannelRead>({
    queryKey: ["channel", id],
    queryFn: () => api.get<ChannelRead>(`/api/v1/channels/${id}`),
    enabled: Boolean(id),
  });
}

export function useCreateChannel() {
  const qc = useQueryClient();
  return useMutation<ChannelRead, unknown, ChannelCreateInput>({
    mutationFn: (input) => api.post<ChannelRead>("/api/v1/channels", input),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["channels"] }),
  });
}

export function useUpdateChannel(id: string) {
  const qc = useQueryClient();
  return useMutation<ChannelRead, unknown, ChannelUpdateInput>({
    mutationFn: (input) =>
      api.patch<ChannelRead>(`/api/v1/channels/${id}`, input),
    onSuccess: (updated) => {
      qc.setQueryData(["channel", id], updated);
      qc.invalidateQueries({ queryKey: ["channels"] });
    },
  });
}

export function useDeleteChannel() {
  const qc = useQueryClient();
  return useMutation<void, unknown, string>({
    mutationFn: (id) => api.delete(`/api/v1/channels/${id}`),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["channels"] }),
  });
}

export function useRotateChannelToken(id: string) {
  const qc = useQueryClient();
  return useMutation<ChannelRead, unknown, void>({
    mutationFn: () =>
      api.post<ChannelRead>(`/api/v1/channels/${id}/rotate-token`, {}),
    onSuccess: (updated) => {
      qc.setQueryData(["channel", id], updated);
      qc.invalidateQueries({ queryKey: ["channels"] });
    },
  });
}
