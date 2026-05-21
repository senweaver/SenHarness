"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "@/lib/api";
import { useAuthStore } from "@/stores/auth-store";
import { useWorkspaceStore } from "@/stores/workspace-store";

export type SkillPackState =
  | "draft"
  | "candidate"
  | "active"
  | "stale"
  | "pinned"
  | "archived"
  | "superseded"
  | "deprecated"
  | "rejected"
  | "tombstone";

export type SkillActorKind = "user" | "curator" | "system" | "evolver";

export interface SkillPackPersisted {
  id: string;
  workspace_id: string;
  slug: string;
  name: string;
  description: string | null;
  version: string;
  state: SkillPackState;
  pinned: boolean;
  last_used_at: string | null;
  effectiveness_avg: number | null;
  content_hash: string | null;
  state_changed_at: string | null;
  state_changed_by: string | null;
  enabled: boolean;
  created_at: string;
  updated_at: string;
}

export interface SkillTransitionEntry {
  from_state: SkillPackState | null;
  to_state: SkillPackState | null;
  reason: string | null;
  actor_identity_id: string | null;
  actor_kind: SkillActorKind | string | null;
  occurred_at: string;
}

export interface SkillStateResponse {
  pack_id: string;
  state: SkillPackState;
  pinned: boolean;
  state_changed_at: string | null;
  state_changed_by: string | null;
  last_transition: SkillTransitionEntry | null;
}

export interface SkillTransitionList {
  pack_id: string;
  items: SkillTransitionEntry[];
}

interface ActionPayload {
  reason?: string;
}

function packKeys(workspaceId: string | null) {
  return {
    all: ["skill-packs", workspaceId] as const,
    state: (id: string) => ["skill-packs", workspaceId, id, "state"] as const,
    transitions: (id: string) =>
      ["skill-packs", workspaceId, id, "transitions"] as const,
  };
}

export function useSkillState(packId: string | null | undefined) {
  const tok = useAuthStore((s) => s.accessToken);
  const ws = useWorkspaceStore((s) => s.activeWorkspaceId);
  return useQuery<SkillStateResponse>({
    queryKey: packKeys(ws).state(packId ?? ""),
    queryFn: () =>
      api.get<SkillStateResponse>(`/api/v1/skills/packs/${packId}/state`),
    enabled: Boolean(tok && ws && packId),
  });
}

export function useSkillTransitions(packId: string | null | undefined) {
  const tok = useAuthStore((s) => s.accessToken);
  const ws = useWorkspaceStore((s) => s.activeWorkspaceId);
  return useQuery<SkillTransitionList>({
    queryKey: packKeys(ws).transitions(packId ?? ""),
    queryFn: () =>
      api.get<SkillTransitionList>(
        `/api/v1/skills/packs/${packId}/transitions`,
      ),
    enabled: Boolean(tok && ws && packId),
  });
}

function useLifecycleAction(verb: "pin" | "unpin" | "archive" | "restore" | "deprecate") {
  const qc = useQueryClient();
  const ws = useWorkspaceStore((s) => s.activeWorkspaceId);
  return useMutation<
    SkillPackPersisted,
    unknown,
    { packId: string; reason?: string }
  >({
    mutationFn: ({ packId, reason }) =>
      api.post<SkillPackPersisted>(
        `/api/v1/skills/packs/${packId}/${verb}`,
        { reason } satisfies ActionPayload,
      ),
    onSuccess: (_data, vars) => {
      qc.invalidateQueries({ queryKey: packKeys(ws).all });
      qc.invalidateQueries({ queryKey: packKeys(ws).state(vars.packId) });
      qc.invalidateQueries({ queryKey: packKeys(ws).transitions(vars.packId) });
    },
  });
}

export function usePinSkill() {
  return useLifecycleAction("pin");
}

export function useUnpinSkill() {
  return useLifecycleAction("unpin");
}

export function useArchiveSkill() {
  return useLifecycleAction("archive");
}

export function useRestoreSkill() {
  return useLifecycleAction("restore");
}

export function useDeprecateSkill() {
  return useLifecycleAction("deprecate");
}

export function useTransitionSkill() {
  const qc = useQueryClient();
  const ws = useWorkspaceStore((s) => s.activeWorkspaceId);
  return useMutation<
    SkillPackPersisted,
    unknown,
    { packId: string; target_state: SkillPackState; reason: string }
  >({
    mutationFn: ({ packId, target_state, reason }) =>
      api.post<SkillPackPersisted>(
        `/api/v1/skills/packs/${packId}/transitions`,
        { target_state, reason },
      ),
    onSuccess: (_data, vars) => {
      qc.invalidateQueries({ queryKey: packKeys(ws).all });
      qc.invalidateQueries({ queryKey: packKeys(ws).state(vars.packId) });
      qc.invalidateQueries({ queryKey: packKeys(ws).transitions(vars.packId) });
    },
  });
}

export function useSkillPacks() {
  const tok = useAuthStore((s) => s.accessToken);
  const ws = useWorkspaceStore((s) => s.activeWorkspaceId);
  return useQuery<SkillPackPersisted[]>({
    queryKey: packKeys(ws).all,
    queryFn: () => api.get<SkillPackPersisted[]>("/api/v1/skills/packs"),
    enabled: Boolean(tok && ws),
  });
}
