"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api } from "@/lib/api";
import { useAuthStore } from "@/stores/auth-store";
import { useWorkspaceStore } from "@/stores/workspace-store";

export const USER_PROFILE_DIMENSIONS = [
  "communication_style",
  "domain_expertise",
  "decision_preference",
  "tone_preference",
  "language_primary",
  "working_hours",
  "autonomy_tolerance",
  "detail_preference",
  "formality",
  "proactivity_tolerance",
  "domain_interest",
  "goal_pattern",
] as const;

export type UserProfileDimension = (typeof USER_PROFILE_DIMENSIONS)[number];

export interface UserProfileFactRead {
  id: string;
  workspace_id: string;
  identity_id: string;
  dimension: UserProfileDimension;
  fact: string;
  confidence: number;
  source_run_ids: string[];
  superseded_by_id: string | null;
  user_confirmed: boolean;
  user_rejected: boolean;
  created_at: string;
  updated_at: string;
}

export interface UserProfileDimensionView {
  dimension: UserProfileDimension;
  active: UserProfileFactRead | null;
  history: UserProfileFactRead[];
  pending_count: number;
  rejected_count: number;
}

export interface UserProfileBundle {
  workspace_id: string;
  identity_id: string;
  dimensions: UserProfileDimensionView[];
  rendered_chars: number;
  last_extracted_at: string | null;
}

export interface UserProfileExtractNowResult {
  workspace_id: string;
  identity_id: string;
  facts_created: number;
  facts_superseded: number;
  facts_unchanged: number;
  artifacts_examined: number;
  aux_skipped: boolean;
  aux_skip_reason: string | null;
  duration_ms: number;
}

function profileKeys(workspaceId: string | null) {
  return {
    bundle: ["user-profile", workspaceId] as const,
  };
}

export function useUserProfile() {
  const tok = useAuthStore((s) => s.accessToken);
  const ws = useWorkspaceStore((s) => s.activeWorkspaceId);
  return useQuery<UserProfileBundle>({
    queryKey: profileKeys(ws).bundle,
    queryFn: () => api.get<UserProfileBundle>("/api/v1/me/profile"),
    enabled: Boolean(tok && ws),
  });
}

export function useConfirmUserProfileFact() {
  const qc = useQueryClient();
  const ws = useWorkspaceStore((s) => s.activeWorkspaceId);
  return useMutation<UserProfileFactRead, unknown, { factId: string }>({
    mutationFn: ({ factId }) =>
      api.post<UserProfileFactRead>(`/api/v1/me/profile/${factId}/confirm`),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: profileKeys(ws).bundle });
    },
  });
}

export function useRejectUserProfileFact() {
  const qc = useQueryClient();
  const ws = useWorkspaceStore((s) => s.activeWorkspaceId);
  return useMutation<UserProfileFactRead, unknown, { factId: string }>({
    mutationFn: ({ factId }) =>
      api.post<UserProfileFactRead>(`/api/v1/me/profile/${factId}/reject`),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: profileKeys(ws).bundle });
    },
  });
}

export function useExtractUserProfileNow() {
  const qc = useQueryClient();
  const ws = useWorkspaceStore((s) => s.activeWorkspaceId);
  return useMutation<UserProfileExtractNowResult, unknown, void>({
    mutationFn: () =>
      api.post<UserProfileExtractNowResult>("/api/v1/me/profile/extract-now"),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: profileKeys(ws).bundle });
    },
  });
}
