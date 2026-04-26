"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "@/lib/api";
import { useAuthStore } from "@/stores/auth-store";
import { useWorkspaceStore } from "@/stores/workspace-store";

export interface MemberRead {
  id: string;
  workspace_id: string;
  identity_id: string;
  role: string;
  department_id: string | null;
  status: string;
  created_at: string;
  updated_at: string;
  identity_name: string | null;
  identity_email: string | null;
  identity_avatar_url: string | null;
}

export interface InvitationRead {
  id: string;
  workspace_id: string;
  code: string;
  email: string | null;
  role: string;
  department_id: string | null;
  expires_at: string | null;
  used_at: string | null;
  created_at: string;
  updated_at: string;
}

export function useMembers() {
  const tok = useAuthStore((s) => s.accessToken);
  const ws = useWorkspaceStore((s) => s.activeWorkspaceId);
  return useQuery<MemberRead[]>({
    queryKey: ["members", ws],
    queryFn: () => api.get<MemberRead[]>(`/api/v1/workspaces/${ws}/members`),
    enabled: Boolean(tok && ws),
  });
}

export function useInvitations() {
  const tok = useAuthStore((s) => s.accessToken);
  const ws = useWorkspaceStore((s) => s.activeWorkspaceId);
  return useQuery<InvitationRead[]>({
    queryKey: ["invitations", ws],
    queryFn: () => api.get<InvitationRead[]>(`/api/v1/workspaces/${ws}/invitations`),
    enabled: Boolean(tok && ws),
  });
}

export function useCreateInvitation() {
  const qc = useQueryClient();
  const ws = useWorkspaceStore((s) => s.activeWorkspaceId);
  return useMutation<
    InvitationRead,
    unknown,
    { email?: string | null; role: string; expires_in_hours: number }
  >({
    mutationFn: (body) =>
      api.post<InvitationRead>(`/api/v1/workspaces/${ws}/invitations`, body),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["invitations", ws] }),
  });
}

export function useRevokeInvitation() {
  const qc = useQueryClient();
  const ws = useWorkspaceStore((s) => s.activeWorkspaceId);
  return useMutation<void, unknown, string>({
    mutationFn: (invitationId) =>
      api.delete(`/api/v1/workspaces/${ws}/invitations/${invitationId}`),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["invitations", ws] }),
  });
}

export function useUpdateMember() {
  const qc = useQueryClient();
  const ws = useWorkspaceStore((s) => s.activeWorkspaceId);
  return useMutation<
    MemberRead,
    unknown,
    {
      identity_id: string;
      role?: string;
      status?: string;
      department_id?: string | null;
    }
  >({
    mutationFn: ({ identity_id, ...patch }) =>
      api.patch<MemberRead>(`/api/v1/workspaces/${ws}/members/${identity_id}`, patch),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["members", ws] }),
  });
}

export function useRemoveMember() {
  const qc = useQueryClient();
  const ws = useWorkspaceStore((s) => s.activeWorkspaceId);
  return useMutation<void, unknown, string>({
    mutationFn: (identity_id) =>
      api.delete(`/api/v1/workspaces/${ws}/members/${identity_id}`),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["members", ws] }),
  });
}
