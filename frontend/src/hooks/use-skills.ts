"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "@/lib/api";
import { useAuthStore } from "@/stores/auth-store";
import { useWorkspaceStore } from "@/stores/workspace-store";

export interface SkillRead {
  slug: string;
  name: string;
  description: string;
  source: "bundled" | "workspace";
  prompt_preview: string;
  body_length: number;
}

export interface SkillDetail extends SkillRead {
  content: string;
}

export function useSkills() {
  const tok = useAuthStore((s) => s.accessToken);
  const ws = useWorkspaceStore((s) => s.activeWorkspaceId);
  return useQuery<SkillRead[]>({
    queryKey: ["skills", ws],
    queryFn: () => api.get<SkillRead[]>("/api/v1/skills"),
    enabled: Boolean(tok && ws),
  });
}

export function useSkillDetail(
  source: "bundled" | "workspace" | null | undefined,
  slug: string | null | undefined,
) {
  return useQuery<SkillDetail>({
    queryKey: ["skill", source, slug],
    queryFn: () =>
      api.get<SkillDetail>(`/api/v1/skills/${source}/${slug}`),
    enabled: Boolean(source && slug),
  });
}

export function useUploadSkill() {
  const qc = useQueryClient();
  return useMutation<SkillRead, unknown, { slug: string; content: string }>({
    mutationFn: (input) => api.post<SkillRead>("/api/v1/skills", input),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["skills"] }),
  });
}

export function useDeleteSkill() {
  const qc = useQueryClient();
  return useMutation<void, unknown, string>({
    mutationFn: (slug) => api.delete(`/api/v1/skills/workspace/${slug}`),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["skills"] }),
  });
}
