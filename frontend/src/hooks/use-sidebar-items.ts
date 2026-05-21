"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "@/lib/api";
import { useAuthStore } from "@/stores/auth-store";
import { useWorkspaceStore } from "@/stores/workspace-store";
import type {
  SidebarItem,
  SidebarItemType,
  SidebarItemsResponse,
} from "@/types/api";

const SIDEBAR_LIMIT = 50;

function sidebarKey(workspaceId: string | null) {
  return ["sidebar", "my-items", workspaceId] as const;
}

export function useSidebarItems() {
  const token = useAuthStore((s) => s.accessToken);
  const workspaceId = useWorkspaceStore((s) => s.activeWorkspaceId);
  return useQuery<SidebarItemsResponse>({
    queryKey: sidebarKey(workspaceId),
    queryFn: () =>
      api.get<SidebarItemsResponse>(
        `/api/v1/sidebar/my-items?limit=${SIDEBAR_LIMIT}`,
      ),
    enabled: Boolean(token && workspaceId),
    staleTime: 30_000,
  });
}

function starEndpoint(type: SidebarItemType, id: string): string {
  switch (type) {
    case "agent":
      return `/api/v1/agents/${id}/star`;
    case "squad":
      return `/api/v1/squads/${id}/star`;
    case "session":
      return `/api/v1/sessions/${id}/star`;
  }
}

export interface PinMutationInput {
  type: SidebarItemType;
  id: string;
  pinned: boolean;
}

export function useTogglePin() {
  const queryClient = useQueryClient();
  const workspaceId = useWorkspaceStore((s) => s.activeWorkspaceId);

  return useMutation<
    void,
    unknown,
    PinMutationInput,
    { previous: SidebarItemsResponse | undefined }
  >({
    mutationFn: async ({ type, id, pinned }) => {
      const endpoint = starEndpoint(type, id);
      const suffix = pinned ? "?pinned=true" : "";
      await api.post(`${endpoint}${suffix}`, {});
    },
    onMutate: async ({ id, pinned }) => {
      const key = sidebarKey(workspaceId);
      await queryClient.cancelQueries({ queryKey: key });
      const previous = queryClient.getQueryData<SidebarItemsResponse>(key);
      if (previous) {
        queryClient.setQueryData<SidebarItemsResponse>(key, {
          ...previous,
          items: previous.items.map((item) =>
            item.id === id ? { ...item, pinned } : item,
          ),
        });
      }
      return { previous };
    },
    onError: (_err, _vars, ctx) => {
      const key = sidebarKey(workspaceId);
      if (ctx?.previous) {
        queryClient.setQueryData(key, ctx.previous);
      }
    },
    onSettled: () => {
      queryClient.invalidateQueries({ queryKey: sidebarKey(workspaceId) });
    },
  });
}

export function useUnstarItem() {
  const queryClient = useQueryClient();
  const workspaceId = useWorkspaceStore((s) => s.activeWorkspaceId);

  return useMutation<
    void,
    unknown,
    Pick<PinMutationInput, "type" | "id">,
    { previous: SidebarItemsResponse | undefined; removed: SidebarItem | null }
  >({
    mutationFn: async ({ type, id }) => {
      await api.delete(starEndpoint(type, id));
    },
    onMutate: async ({ id }) => {
      const key = sidebarKey(workspaceId);
      await queryClient.cancelQueries({ queryKey: key });
      const previous = queryClient.getQueryData<SidebarItemsResponse>(key);
      const removed = previous?.items.find((item) => item.id === id) ?? null;
      if (previous) {
        queryClient.setQueryData<SidebarItemsResponse>(key, {
          ...previous,
          items: previous.items.filter((item) => item.id !== id),
          total: Math.max(previous.total - 1, 0),
        });
      }
      return { previous, removed };
    },
    onError: (_err, _vars, ctx) => {
      const key = sidebarKey(workspaceId);
      if (ctx?.previous) {
        queryClient.setQueryData(key, ctx.previous);
      }
    },
    onSettled: () => {
      queryClient.invalidateQueries({ queryKey: sidebarKey(workspaceId) });
    },
  });
}
