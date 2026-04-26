"use client";

import { useQuery } from "@tanstack/react-query";
import { api } from "@/lib/api";
import type { MeOut } from "@/types/api";
import { useAuthStore } from "@/stores/auth-store";
import { useWorkspaceStore } from "@/stores/workspace-store";
import { useEffect } from "react";

export function useMe() {
  const accessToken = useAuthStore((s) => s.accessToken);
  const setWorkspaces = useWorkspaceStore((s) => s.setWorkspaces);
  const setActive = useWorkspaceStore((s) => s.setActive);
  const active = useWorkspaceStore((s) => s.activeWorkspaceId);

  const query = useQuery<MeOut>({
    queryKey: ["me"],
    queryFn: () => api.get<MeOut>("/api/v1/me"),
    enabled: Boolean(accessToken),
  });

  useEffect(() => {
    if (!query.data) return;
    setWorkspaces(
      query.data.workspaces.map((w) => ({
        id: w.workspace_id,
        name: w.workspace_name,
        slug: w.workspace_slug,
        role: w.role,
      })),
    );
    if (!active && query.data.current_workspace_id) {
      setActive(query.data.current_workspace_id);
    }
  }, [query.data, active, setWorkspaces, setActive]);

  return query;
}
