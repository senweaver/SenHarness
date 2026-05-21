"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "@/lib/api";
import { useAuthStore } from "@/stores/auth-store";
import { useWorkspaceStore } from "@/stores/workspace-store";
import type {
  SquadMemberRead,
  SquadRead,
  SquadReadWithMembers,
  SquadStrategy,
} from "@/types/api";

export interface SquadMemberInput {
  agent_id: string;
  role_in_squad?: string;
  weight?: number;
}

export interface SquadCreateInput {
  name: string;
  description?: string | null;
  strategy?: SquadStrategy;
  config_json?: Record<string, unknown>;
  members?: SquadMemberInput[];
}

export interface SquadUpdateInput {
  name?: string;
  description?: string | null;
  strategy?: SquadStrategy;
  config_json?: Record<string, unknown>;
}

export function useSquads() {
  const token = useAuthStore((s) => s.accessToken);
  const ws = useWorkspaceStore((s) => s.activeWorkspaceId);
  return useQuery<SquadRead[]>({
    queryKey: ["squads", "list", ws],
    queryFn: () => api.get<SquadRead[]>("/api/v1/squads"),
    enabled: Boolean(token && ws),
  });
}

export function useSquad(squadId: string | null | undefined) {
  return useQuery<SquadReadWithMembers>({
    queryKey: ["squad", squadId],
    queryFn: () =>
      api.get<SquadReadWithMembers>(`/api/v1/squads/${squadId}`),
    enabled: Boolean(squadId),
  });
}

export function useCreateSquad() {
  const qc = useQueryClient();
  return useMutation<SquadReadWithMembers, unknown, SquadCreateInput>({
    mutationFn: (input) =>
      api.post<SquadReadWithMembers>("/api/v1/squads", input),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["squads"] });
    },
  });
}

export function useUpdateSquad(squadId: string) {
  const qc = useQueryClient();
  return useMutation<SquadReadWithMembers, unknown, SquadUpdateInput>({
    mutationFn: (input) =>
      api.patch<SquadReadWithMembers>(`/api/v1/squads/${squadId}`, input),
    onSuccess: (updated) => {
      qc.setQueryData(["squad", squadId], updated);
      qc.invalidateQueries({ queryKey: ["squads"] });
    },
  });
}

export function useReplaceSquadMembers(squadId: string) {
  const qc = useQueryClient();
  return useMutation<SquadMemberRead[], unknown, SquadMemberInput[]>({
    mutationFn: (members) =>
      api.put<SquadMemberRead[]>(`/api/v1/squads/${squadId}/members`, members),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["squad", squadId] });
      qc.invalidateQueries({ queryKey: ["squads"] });
    },
  });
}

export function useDeleteSquad() {
  const qc = useQueryClient();
  return useMutation<void, unknown, string>({
    mutationFn: (id) => api.delete(`/api/v1/squads/${id}`),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["squads"] });
    },
  });
}
