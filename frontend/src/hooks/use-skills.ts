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

/**
 * Import a SKILL.md from a public URL or GitHub blob/tree URL. The
 * fetch happens server-side to avoid browser CORS for arbitrary
 * hosts; GitHub URLs are normalised to `raw.githubusercontent.com`
 * by the backend.
 */
export function useImportSkillFromUrl() {
  const qc = useQueryClient();
  return useMutation<
    SkillRead,
    unknown,
    { url: string; slug?: string | null }
  >({
    mutationFn: (input) =>
      api.post<SkillRead>("/api/v1/skills/import-url", {
        url: input.url,
        slug: input.slug ?? null,
      }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["skills"] }),
  });
}

/**
 * Import a folder-shaped Skill bundle (Anthropic Agent Skills
 * standard: SKILL.md + reference docs + scripts in a single ZIP).
 * The ZIP is uploaded as multipart/form-data so the backend can
 * extract it under `{STORAGE}/skills/<workspace>/<slug>/` with the
 * full directory structure preserved.
 */
export function useImportSkillFromBundle() {
  const qc = useQueryClient();
  return useMutation<
    SkillRead,
    unknown,
    { file: File; slug?: string | null }
  >({
    mutationFn: async ({ file, slug }) => {
      const fd = new FormData();
      fd.append("file", file);
      if (slug) fd.append("slug", slug);
      return api.post<SkillRead>("/api/v1/skills/import-bundle", fd);
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: ["skills"] }),
  });
}
