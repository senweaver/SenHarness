"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "@/lib/api";
import { useAuthStore } from "@/stores/auth-store";
import { useWorkspaceStore } from "@/stores/workspace-store";
import type { DepartmentRead } from "@/types/api";

export function useDepartments() {
  const tok = useAuthStore((s) => s.accessToken);
  const ws = useWorkspaceStore((s) => s.activeWorkspaceId);
  return useQuery<DepartmentRead[]>({
    queryKey: ["departments", ws],
    queryFn: () => api.get<DepartmentRead[]>("/api/v1/departments"),
    enabled: Boolean(tok && ws),
  });
}

export function useCreateDepartment() {
  const qc = useQueryClient();
  const ws = useWorkspaceStore((s) => s.activeWorkspaceId);
  return useMutation<
    DepartmentRead,
    unknown,
    { name: string; parent_id?: string | null }
  >({
    mutationFn: (body) => api.post<DepartmentRead>("/api/v1/departments", body),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["departments", ws] }),
  });
}

export function useUpdateDepartment() {
  const qc = useQueryClient();
  const ws = useWorkspaceStore((s) => s.activeWorkspaceId);
  return useMutation<
    DepartmentRead,
    unknown,
    { id: string; name?: string; parent_id?: string | null }
  >({
    mutationFn: ({ id, ...patch }) =>
      api.patch<DepartmentRead>(`/api/v1/departments/${id}`, patch),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["departments", ws] }),
  });
}

export function useDeleteDepartment() {
  const qc = useQueryClient();
  const ws = useWorkspaceStore((s) => s.activeWorkspaceId);
  return useMutation<void, unknown, string>({
    mutationFn: (id) => api.delete(`/api/v1/departments/${id}`),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["departments", ws] }),
  });
}
