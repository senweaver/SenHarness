"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect } from "react";
import { api } from "@/lib/api";
import { useWorkspaceStore, type WorkspaceBrief } from "@/stores/workspace-store";

export interface WorkspaceRead {
  id: string;
  name: string;
  slug: string;
  description: string | null;
  plan: string;
  branding_json: Record<string, unknown>;
  home_config_json: Record<string, unknown>;
  created_at: string;
  updated_at: string;
}

export interface WorkspaceUpdate {
  name?: string;
  description?: string;
  branding_json?: Record<string, unknown>;
  home_config_json?: Record<string, unknown>;
}

export function useActiveWorkspace() {
  const activeId = useWorkspaceStore((s) => s.activeWorkspaceId);
  return useQuery<WorkspaceRead | null>({
    queryKey: ["workspace", activeId],
    queryFn: async () => {
      if (!activeId) return null;
      return api.get<WorkspaceRead>(`/api/v1/workspaces/${activeId}`);
    },
    enabled: Boolean(activeId),
  });
}

export function useUpdateWorkspace() {
  const activeId = useWorkspaceStore((s) => s.activeWorkspaceId);
  const qc = useQueryClient();
  const setWorkspaces = useWorkspaceStore((s) => s.setWorkspaces);
  const workspaces = useWorkspaceStore((s) => s.workspaces);

  return useMutation<WorkspaceRead, unknown, WorkspaceUpdate>({
    mutationFn: (patch) =>
      api.patch<WorkspaceRead>(`/api/v1/workspaces/${activeId}`, patch),
    onSuccess: (updated) => {
      qc.setQueryData(["workspace", activeId], updated);
      // Mirror branding into the workspace store so nav/home update instantly.
      const next: WorkspaceBrief[] = workspaces.map((w) =>
        w.id === updated.id
          ? { ...w, name: updated.name, branding: updated.branding_json as WorkspaceBrief["branding"] }
          : w,
      );
      setWorkspaces(next);
      qc.invalidateQueries({ queryKey: ["me"] });
    },
  });
}

/** Convenience: keep workspace store branding in sync with server whenever it loads. */
export function useSyncWorkspaceBranding() {
  const { data } = useActiveWorkspace();
  const workspaces = useWorkspaceStore((s) => s.workspaces);
  const setWorkspaces = useWorkspaceStore((s) => s.setWorkspaces);

  useEffect(() => {
    if (!data) return;
    const current = workspaces.find((w) => w.id === data.id);
    if (!current) return;
    const merged: WorkspaceBrief = {
      ...current,
      name: data.name,
      branding: data.branding_json as WorkspaceBrief["branding"],
    };
    if (JSON.stringify(current) !== JSON.stringify(merged)) {
      setWorkspaces(workspaces.map((w) => (w.id === data.id ? merged : w)));
    }
  }, [data, workspaces, setWorkspaces]);
}
