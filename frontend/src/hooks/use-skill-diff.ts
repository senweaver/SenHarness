"use client";

import { useMutation, useQuery } from "@tanstack/react-query";
import { api, ApiError } from "@/lib/api";
import { useAuthStore } from "@/stores/auth-store";
import { useWorkspaceStore } from "@/stores/workspace-store";
import type {
  SkillDiffRequest,
  SkillDiffResponse,
} from "@/types/api";

export function useComputeSkillDiff() {
  return useMutation<SkillDiffResponse, ApiError, SkillDiffRequest>({
    mutationFn: (input) =>
      api.post<SkillDiffResponse>("/api/v1/skills/diff", input),
  });
}

/**
 * Versioned diff lookup. Returns 501 with code
 * ``skill.versions_not_implemented`` until M1.2 ships the
 * ``skill_pack_versions`` table; the consuming UI must catch the
 * error and surface a "versions not yet available" hint instead of
 * a generic crash.
 */
export function useSkillVersionDiff(
  packId: string | null | undefined,
  versionA: string | null | undefined,
  versionB: string | null | undefined,
) {
  const tok = useAuthStore((s) => s.accessToken);
  const ws = useWorkspaceStore((s) => s.activeWorkspaceId);
  return useQuery<SkillDiffResponse, ApiError>({
    queryKey: ["skill-version-diff", ws, packId, versionA, versionB],
    queryFn: () =>
      api.get<SkillDiffResponse>(
        `/api/v1/skills/packs/${packId}/versions/${versionA}/diff/${versionB}`,
      ),
    enabled: Boolean(tok && ws && packId && versionA && versionB),
    retry: (failureCount, err) => {
      if (err instanceof ApiError && err.status === 501) return false;
      return failureCount < 2;
    },
  });
}
