"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, ApiError } from "@/lib/api";
import { useAuthStore } from "@/stores/auth-store";
import { useWorkspaceStore } from "@/stores/workspace-store";
import type {
  SkillPackVersionList,
  SkillPackVersionRead,
  SkillPackVersionState,
  SkillPackVersionWithContent,
} from "@/types/api";

function versionKeys(workspaceId: string | null, packId: string) {
  return {
    list: ["skill-pack-versions", workspaceId, packId] as const,
    active: ["skill-pack-versions", workspaceId, packId, "active"] as const,
  };
}

export function useSkillVersions(packId: string | null | undefined) {
  const tok = useAuthStore((s) => s.accessToken);
  const ws = useWorkspaceStore((s) => s.activeWorkspaceId);
  return useQuery<SkillPackVersionList, ApiError>({
    queryKey: versionKeys(ws, packId ?? "").list,
    queryFn: () =>
      api.get<SkillPackVersionList>(
        `/api/v1/skills/packs/${packId}/versions`,
      ),
    enabled: Boolean(tok && ws && packId),
  });
}

export function useActiveSkillVersion(packId: string | null | undefined) {
  const tok = useAuthStore((s) => s.accessToken);
  const ws = useWorkspaceStore((s) => s.activeWorkspaceId);
  return useQuery<SkillPackVersionWithContent, ApiError>({
    queryKey: versionKeys(ws, packId ?? "").active,
    queryFn: () =>
      api.get<SkillPackVersionWithContent>(
        `/api/v1/skills/packs/${packId}/versions/active`,
      ),
    enabled: Boolean(tok && ws && packId),
    retry: (failureCount, err) => {
      // 404 means "no version yet" — don't retry on that.
      if (err instanceof ApiError && err.status === 404) return false;
      return failureCount < 2;
    },
  });
}

export function useActivateSkillVersion(packId: string) {
  const qc = useQueryClient();
  const ws = useWorkspaceStore((s) => s.activeWorkspaceId);
  return useMutation<
    SkillPackVersionRead,
    ApiError,
    { versionId: string; reason?: string }
  >({
    mutationFn: ({ versionId, reason }) =>
      api.post<SkillPackVersionRead>(
        `/api/v1/skills/packs/${packId}/versions/${versionId}/activate`,
        { reason },
      ),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: versionKeys(ws, packId).list });
      qc.invalidateQueries({ queryKey: versionKeys(ws, packId).active });
      qc.invalidateQueries({ queryKey: ["skill-packs", ws] });
    },
  });
}

export function useTransitionSkillVersion(packId: string) {
  const qc = useQueryClient();
  const ws = useWorkspaceStore((s) => s.activeWorkspaceId);
  return useMutation<
    SkillPackVersionRead,
    ApiError,
    { versionId: string; target_state: SkillPackVersionState; reason: string }
  >({
    mutationFn: ({ versionId, target_state, reason }) =>
      api.post<SkillPackVersionRead>(
        `/api/v1/skills/packs/${packId}/versions/${versionId}/transition`,
        { target_state, reason },
      ),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: versionKeys(ws, packId).list });
      qc.invalidateQueries({ queryKey: versionKeys(ws, packId).active });
      qc.invalidateQueries({ queryKey: ["skill-packs", ws] });
    },
  });
}

export function useRollbackToVersion(packId: string) {
  const qc = useQueryClient();
  const ws = useWorkspaceStore((s) => s.activeWorkspaceId);
  return useMutation<
    SkillPackVersionRead,
    ApiError,
    { versionId: string; reason: string }
  >({
    mutationFn: ({ versionId, reason }) =>
      api.post<SkillPackVersionRead>(
        `/api/v1/skills/packs/${packId}/versions/${versionId}/rollback`,
        { reason },
      ),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: versionKeys(ws, packId).list });
      qc.invalidateQueries({ queryKey: versionKeys(ws, packId).active });
      qc.invalidateQueries({ queryKey: ["skill-packs", ws] });
    },
  });
}
