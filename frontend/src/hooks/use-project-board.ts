"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api } from "@/lib/api";
import { useAuthStore } from "@/stores/auth-store";
import { useWorkspaceStore } from "@/stores/workspace-store";
import type {
  BoardCardColumnValue,
  BoardCardPriorityValue,
  BoardCardRead,
  BoardKanbanRead,
  ProjectBoardRead,
} from "@/types/api";

export interface ProjectBoardCreateInput {
  name: string;
  description?: string | null;
  squad_id?: string | null;
}

export interface ProjectBoardUpdateInput {
  name?: string;
  description?: string | null;
  squad_id?: string | null;
}

export interface BoardCardCreateInput {
  title: string;
  description?: string | null;
  column?: BoardCardColumnValue;
  priority?: BoardCardPriorityValue;
  assignee_agent_id?: string | null;
  assignee_identity_id?: string | null;
  due_at?: string | null;
}

export interface BoardCardUpdateInput {
  title?: string;
  description?: string | null;
  priority?: BoardCardPriorityValue;
  assignee_agent_id?: string | null;
  assignee_identity_id?: string | null;
  due_at?: string | null;
}

export interface BoardCardMoveInput {
  target_column: BoardCardColumnValue;
  target_position: number;
}

export function useBoards(squadId?: string | null) {
  const token = useAuthStore((s) => s.accessToken);
  const ws = useWorkspaceStore((s) => s.activeWorkspaceId);
  const qs = squadId ? `?squad_id=${encodeURIComponent(squadId)}` : "";
  return useQuery<ProjectBoardRead[]>({
    queryKey: ["boards", "list", ws, squadId ?? null],
    queryFn: () => api.get<ProjectBoardRead[]>(`/api/v1/boards${qs}`),
    enabled: Boolean(token && ws),
  });
}

export function useBoard(boardId: string | null | undefined) {
  const token = useAuthStore((s) => s.accessToken);
  const ws = useWorkspaceStore((s) => s.activeWorkspaceId);
  return useQuery<BoardKanbanRead>({
    queryKey: ["board", ws, boardId],
    queryFn: () => api.get<BoardKanbanRead>(`/api/v1/boards/${boardId}`),
    enabled: Boolean(token && ws && boardId),
  });
}

export function useBoardCards(boardId: string | null | undefined) {
  const token = useAuthStore((s) => s.accessToken);
  const ws = useWorkspaceStore((s) => s.activeWorkspaceId);
  return useQuery<BoardCardRead[]>({
    queryKey: ["board", ws, boardId, "cards"],
    queryFn: () =>
      api.get<BoardCardRead[]>(`/api/v1/boards/${boardId}/cards`),
    enabled: Boolean(token && ws && boardId),
  });
}

export function useCard(cardId: string | null | undefined) {
  const token = useAuthStore((s) => s.accessToken);
  return useQuery<BoardCardRead>({
    queryKey: ["card", cardId],
    queryFn: () => api.get<BoardCardRead>(`/api/v1/cards/${cardId}`),
    enabled: Boolean(token && cardId),
  });
}

export function useAgentCards(agentId: string | null | undefined) {
  const token = useAuthStore((s) => s.accessToken);
  const ws = useWorkspaceStore((s) => s.activeWorkspaceId);
  return useQuery<BoardCardRead[]>({
    queryKey: ["agent-cards", ws, agentId],
    queryFn: () =>
      api.get<BoardCardRead[]>(`/api/v1/agents/${agentId}/cards`),
    enabled: Boolean(token && ws && agentId),
  });
}

export function useCreateBoard() {
  const qc = useQueryClient();
  return useMutation<ProjectBoardRead, unknown, ProjectBoardCreateInput>({
    mutationFn: (input) =>
      api.post<ProjectBoardRead>("/api/v1/boards", input),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["boards"] });
    },
  });
}

export function useUpdateBoard(boardId: string) {
  const qc = useQueryClient();
  return useMutation<ProjectBoardRead, unknown, ProjectBoardUpdateInput>({
    mutationFn: (input) =>
      api.patch<ProjectBoardRead>(`/api/v1/boards/${boardId}`, input),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["boards"] });
      qc.invalidateQueries({ queryKey: ["board"] });
    },
  });
}

export function useArchiveBoard() {
  const qc = useQueryClient();
  return useMutation<ProjectBoardRead, unknown, string>({
    mutationFn: (boardId) =>
      api.post<ProjectBoardRead>(`/api/v1/boards/${boardId}/archive`),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["boards"] });
      qc.invalidateQueries({ queryKey: ["board"] });
    },
  });
}

export function useCreateCard(boardId: string) {
  const qc = useQueryClient();
  return useMutation<BoardCardRead, unknown, BoardCardCreateInput>({
    mutationFn: (input) =>
      api.post<BoardCardRead>(`/api/v1/boards/${boardId}/cards`, input),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["board"] });
      qc.invalidateQueries({ queryKey: ["agent-cards"] });
    },
  });
}

export function useUpdateCard(cardId: string) {
  const qc = useQueryClient();
  return useMutation<BoardCardRead, unknown, BoardCardUpdateInput>({
    mutationFn: (input) =>
      api.patch<BoardCardRead>(`/api/v1/cards/${cardId}`, input),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["board"] });
      qc.invalidateQueries({ queryKey: ["card", cardId] });
      qc.invalidateQueries({ queryKey: ["agent-cards"] });
    },
  });
}

export function useMoveCard() {
  const qc = useQueryClient();
  return useMutation<
    BoardCardRead,
    unknown,
    { cardId: string; payload: BoardCardMoveInput }
  >({
    mutationFn: ({ cardId, payload }) =>
      api.post<BoardCardRead>(`/api/v1/cards/${cardId}/move`, payload),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["board"] });
      qc.invalidateQueries({ queryKey: ["agent-cards"] });
    },
  });
}

export function useArchiveCard() {
  const qc = useQueryClient();
  return useMutation<BoardCardRead, unknown, string>({
    mutationFn: (cardId) =>
      api.post<BoardCardRead>(`/api/v1/cards/${cardId}/archive`),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["board"] });
      qc.invalidateQueries({ queryKey: ["agent-cards"] });
    },
  });
}

export function useCompleteCard() {
  const qc = useQueryClient();
  return useMutation<BoardCardRead, unknown, string>({
    mutationFn: (cardId) =>
      api.post<BoardCardRead>(`/api/v1/cards/${cardId}/complete`),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["board"] });
      qc.invalidateQueries({ queryKey: ["agent-cards"] });
    },
  });
}
