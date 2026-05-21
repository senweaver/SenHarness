import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, renderHook, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { useAuthStore } from "@/stores/auth-store";
import { useWorkspaceStore } from "@/stores/workspace-store";
import type {
  SidebarItem,
  SidebarItemsResponse,
} from "@/types/api";

import {
  useSidebarItems,
  useTogglePin,
  useUnstarItem,
} from "@/hooks/use-sidebar-items";

vi.mock("@/lib/api", () => {
  const api = {
    get: vi.fn(),
    post: vi.fn(),
    patch: vi.fn(),
    put: vi.fn(),
    delete: vi.fn(),
  };
  return { api, apiFetch: vi.fn(), ApiError: class ApiError extends Error {} };
});

import { api } from "@/lib/api";

const apiMock = vi.mocked(api);

function makeItem(overrides: Partial<SidebarItem> & { id: string }): SidebarItem {
  return {
    type: overrides.type ?? "agent",
    id: overrides.id,
    name: overrides.name ?? `Agent ${overrides.id}`,
    avatar_seed: overrides.avatar_seed ?? "A",
    pinned: overrides.pinned ?? false,
    unread_count: overrides.unread_count ?? 0,
    last_activity_at: overrides.last_activity_at ?? null,
    href: overrides.href ?? `/agents/${overrides.id}`,
  };
}

function wrapper(client: QueryClient) {
  return function Wrapper({ children }: { children: ReactNode }) {
    return (
      <QueryClientProvider client={client}>{children}</QueryClientProvider>
    );
  };
}

describe("useSidebarItems", () => {
  beforeEach(() => {
    apiMock.get.mockReset();
    apiMock.post.mockReset();
    apiMock.delete.mockReset();
    useAuthStore.setState({
      accessToken: "test-token",
      accessExpiresAt: null,
      setAccess: useAuthStore.getState().setAccess,
      clear: useAuthStore.getState().clear,
    });
    useWorkspaceStore.setState({
      workspaces: [],
      activeWorkspaceId: "ws-1",
      setWorkspaces: useWorkspaceStore.getState().setWorkspaces,
      setActive: useWorkspaceStore.getState().setActive,
      clear: useWorkspaceStore.getState().clear,
    });
  });

  it("loads the list of items", async () => {
    const items: SidebarItem[] = [makeItem({ id: "a-1" })];
    apiMock.get.mockResolvedValueOnce({ items, total: 1 } as SidebarItemsResponse);

    const queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });
    const { result } = renderHook(() => useSidebarItems(), {
      wrapper: wrapper(queryClient),
    });

    await waitFor(() => {
      expect(result.current.isSuccess).toBe(true);
    });
    expect(result.current.data?.items).toHaveLength(1);
    expect(result.current.data?.items[0]?.id).toBe("a-1");
  });

  it("surfaces error state when the request fails", async () => {
    apiMock.get.mockRejectedValueOnce(new Error("boom"));
    const queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });
    const { result } = renderHook(() => useSidebarItems(), {
      wrapper: wrapper(queryClient),
    });
    await waitFor(() => {
      expect(result.current.isError).toBe(true);
    });
  });

  it("optimistically toggles pin then rolls back on error", async () => {
    const item = makeItem({ id: "a-2", pinned: false });
    apiMock.get.mockResolvedValueOnce({
      items: [item],
      total: 1,
    } as SidebarItemsResponse);
    apiMock.post.mockRejectedValueOnce(new Error("nope"));
    apiMock.get.mockResolvedValue({
      items: [item],
      total: 1,
    } as SidebarItemsResponse);

    const queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });
    const wrap = wrapper(queryClient);
    const { result: list } = renderHook(() => useSidebarItems(), { wrapper: wrap });
    await waitFor(() => {
      expect(list.current.isSuccess).toBe(true);
    });

    const { result: pin } = renderHook(() => useTogglePin(), { wrapper: wrap });

    await act(async () => {
      await pin.current
        .mutateAsync({ type: "agent", id: "a-2", pinned: true })
        .catch(() => undefined);
    });

    expect(list.current.data?.items[0]?.pinned).toBe(false);
  });

  it("removes the item from the cache on unstar (and rolls back on error)", async () => {
    const itemA = makeItem({ id: "a-3" });
    const itemB = makeItem({ id: "a-4" });
    apiMock.get.mockResolvedValueOnce({
      items: [itemA, itemB],
      total: 2,
    } as SidebarItemsResponse);
    apiMock.delete.mockRejectedValueOnce(new Error("server down"));
    apiMock.get.mockResolvedValue({
      items: [itemA, itemB],
      total: 2,
    } as SidebarItemsResponse);

    const queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });
    const wrap = wrapper(queryClient);
    const { result: list } = renderHook(() => useSidebarItems(), { wrapper: wrap });
    await waitFor(() => {
      expect(list.current.isSuccess).toBe(true);
    });

    const { result: unstar } = renderHook(() => useUnstarItem(), { wrapper: wrap });

    await act(async () => {
      await unstar.current
        .mutateAsync({ type: "agent", id: "a-3" })
        .catch(() => undefined);
    });

    expect(list.current.data?.items.map((i) => i.id)).toEqual(["a-3", "a-4"]);
  });
});
