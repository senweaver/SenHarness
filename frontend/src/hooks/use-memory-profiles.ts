"use client";

/**
 * Memory profile hooks — workspace MEMORY.md + per-identity USER.md + SOUL.md.
 *
 * Backend: `backend/app/api/v1/memory_profiles.py` (8 endpoints).
 *
 * Three profile "kinds" share one table:
 *
 * - `workspace_memory` — workspace knowledge core. Admin-editable. One per
 *   workspace. Useful for "facts every agent in this company should start
 *   from".
 * - `user_profile` (`USER.md`) — self-editable per identity. How you want
 *   to be addressed, worked with, which tools to skip.
 * - `user_soul` (`SOUL.md`) — passive user modelling over 12 canonical
 *   dimensions. Writes **never** go straight in; they land in
 *   `pending_updates_json` and only activate once the identity (or an
 *   admin) approves.
 */

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "@/lib/api";
import { useAuthStore } from "@/stores/auth-store";
import { useWorkspaceStore } from "@/stores/workspace-store";

export type MemoryProfileKind =
  | "workspace_memory"
  | "user_profile"
  | "user_soul";

export interface SoulPending {
  id: string;
  proposed_content: string;
  proposed_dims: Record<string, unknown>;
  proposed_at: string;
  proposed_by_identity_id: string | null;
  source_session_id: string | null;
  rationale: string;
}

export interface MemoryProfileRead {
  id: string;
  workspace_id: string;
  kind: MemoryProfileKind;
  subject_id: string;
  identity_id: string | null;
  content_md: string;
  char_count: number;
  soul_dims_json: Record<string, unknown>;
  pending_updates_json: SoulPending[];
  metadata_json: Record<string, unknown>;
  updated_by: string | null;
  created_at: string;
  updated_at: string;
}

export interface MemoryProfileUpsert {
  content_md: string;
  soul_dims_json?: Record<string, unknown> | null;
  metadata_json?: Record<string, unknown> | null;
}

export interface MyMemoryProfiles {
  profile: MemoryProfileRead | null;
  soul: MemoryProfileRead | null;
}

export interface IdentityProfiles extends MyMemoryProfiles {
  identity_id: string;
}

export interface SoulProposalInput {
  proposed_content: string;
  proposed_dims?: Record<string, unknown>;
  source_session_id?: string | null;
  rationale?: string;
}

// ─── Character caps (mirrors backend MAX_CONTENT_CHARS) ──
export const MEMORY_CAPS: Record<MemoryProfileKind, number> = {
  workspace_memory: 2200,
  user_profile: 1375,
  user_soul: 2000,
};

// ─── Workspace MEMORY.md ─────────────────────────────────

export function useWorkspaceMemory() {
  const tok = useAuthStore((s) => s.accessToken);
  const ws = useWorkspaceStore((s) => s.activeWorkspaceId);
  return useQuery<MemoryProfileRead | null>({
    queryKey: ["memory-profiles", "workspace", ws],
    queryFn: () =>
      api.get<MemoryProfileRead | null>(`/api/v1/memory-profiles/workspace`),
    enabled: Boolean(tok && ws),
  });
}

export function usePutWorkspaceMemory() {
  const qc = useQueryClient();
  return useMutation<MemoryProfileRead, unknown, MemoryProfileUpsert>({
    mutationFn: (input) =>
      api.put<MemoryProfileRead>(`/api/v1/memory-profiles/workspace`, input),
    onSuccess: () =>
      qc.invalidateQueries({ queryKey: ["memory-profiles", "workspace"] }),
  });
}

// ─── Self: USER.md + SOUL.md ─────────────────────────────

export function useMyMemoryProfiles() {
  const tok = useAuthStore((s) => s.accessToken);
  const ws = useWorkspaceStore((s) => s.activeWorkspaceId);
  return useQuery<MyMemoryProfiles>({
    queryKey: ["memory-profiles", "me", ws],
    queryFn: () => api.get<MyMemoryProfiles>(`/api/v1/memory-profiles/me`),
    enabled: Boolean(tok && ws),
  });
}

export function usePutMyProfile() {
  const qc = useQueryClient();
  return useMutation<MemoryProfileRead, unknown, MemoryProfileUpsert>({
    mutationFn: (input) =>
      api.put<MemoryProfileRead>(`/api/v1/memory-profiles/me/profile`, input),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["memory-profiles", "me"] }),
  });
}

// ─── SOUL pending queue ──────────────────────────────────

export function useMySoulPending() {
  const tok = useAuthStore((s) => s.accessToken);
  const ws = useWorkspaceStore((s) => s.activeWorkspaceId);
  return useQuery<SoulPending[]>({
    queryKey: ["memory-profiles", "me", "soul-pending", ws],
    queryFn: () =>
      api.get<SoulPending[]>(`/api/v1/memory-profiles/me/soul/pending`),
    enabled: Boolean(tok && ws),
  });
}

export function useProposeSoul() {
  const qc = useQueryClient();
  return useMutation<SoulPending, unknown, SoulProposalInput>({
    mutationFn: (input) =>
      api.post<SoulPending>(`/api/v1/memory-profiles/me/soul/propose`, input),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["memory-profiles", "me"] });
    },
  });
}

export function useDecideSoul() {
  const qc = useQueryClient();
  return useMutation<
    MemoryProfileRead,
    unknown,
    { proposalId: string; decision: "approve" | "reject"; reason?: string }
  >({
    mutationFn: ({ proposalId, decision, reason }) =>
      api.post<MemoryProfileRead>(
        `/api/v1/memory-profiles/me/soul/${proposalId}/decide`,
        { decision, reason: reason ?? "" },
      ),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["memory-profiles", "me"] });
    },
  });
}

// ─── Admin: read someone else's bundle ───────────────────

export function useIdentityProfiles(identityId: string) {
  const tok = useAuthStore((s) => s.accessToken);
  const ws = useWorkspaceStore((s) => s.activeWorkspaceId);
  return useQuery<IdentityProfiles>({
    queryKey: ["memory-profiles", "identity", ws, identityId],
    queryFn: () =>
      api.get<IdentityProfiles>(
        `/api/v1/memory-profiles/identities/${identityId}`,
      ),
    enabled: Boolean(tok && ws && identityId),
  });
}
