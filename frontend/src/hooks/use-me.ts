"use client";

import { useQuery } from "@tanstack/react-query";
import { api } from "@/lib/api";
import type { MeOut } from "@/types/api";
import { useAuthStore } from "@/stores/auth-store";
import { useWorkspaceStore } from "@/stores/workspace-store";
import { useOnboardingStore } from "@/stores/onboarding-store";
import { useEffect } from "react";

export function useMe() {
  const accessToken = useAuthStore((s) => s.accessToken);
  const setWorkspaces = useWorkspaceStore((s) => s.setWorkspaces);
  const setActive = useWorkspaceStore((s) => s.setActive);
  const bindWorkspaceIdentity = useWorkspaceStore((s) => s.bindIdentity);
  const active = useWorkspaceStore((s) => s.activeWorkspaceId);
  const bindOnboardingIdentity = useOnboardingStore((s) => s.bindIdentity);

  const query = useQuery<MeOut>({
    queryKey: ["me"],
    queryFn: () => api.get<MeOut>("/api/v1/me"),
    enabled: Boolean(accessToken),
  });

  useEffect(() => {
    if (!query.data) return;
    // Detect account switch in the same browser (logout + login as a
    // different identity, or a refresh after token swap). The store's
    // ``bindIdentity`` no-ops when the id matches and wipes persisted
    // state when it differs, so the next user can't inherit the
    // previous user's ``activeWorkspaceId`` / onboarding draft.
    bindWorkspaceIdentity(query.data.id);
    bindOnboardingIdentity(query.data.id);

    setWorkspaces(
      query.data.workspaces.map((w) => ({
        id: w.workspace_id,
        name: w.workspace_name,
        slug: w.workspace_slug,
        role: w.role,
      })),
    );

    const memberships = new Set(
      query.data.workspaces.map((w) => w.workspace_id),
    );
    if (active && !memberships.has(active)) {
      // Stored workspace no longer belongs to this user — drop it and
      // fall back to whatever the server picked (or null when the
      // account has zero memberships).
      setActive(query.data.current_workspace_id ?? null);
    } else if (!active && query.data.current_workspace_id) {
      setActive(query.data.current_workspace_id);
    }
  }, [
    query.data,
    active,
    setWorkspaces,
    setActive,
    bindWorkspaceIdentity,
    bindOnboardingIdentity,
  ]);

  return query;
}
