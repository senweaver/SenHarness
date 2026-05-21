/**
 * Subscription wiring contract for ``useAgentRuntimeSummariesStream``:
 *
 *   1. Without an access token, no WebSocket is constructed.
 *   2. With a token but no workspaces in the store, no WebSocket is
 *      constructed (the workspace switcher renders nothing yet, so
 *      there is nothing to multiplex over).
 *   3. With both, exactly one WebSocket is opened and the URL includes
 *      a ``subscribe_workspaces`` query string containing every id in
 *      the store, comma-separated.
 *
 * The query hook itself is a thin wrapper around ``api.get`` — its
 * happy-path is covered indirectly by the component test that depends
 * on it.
 */
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { renderHook } from "@testing-library/react";
import type { ReactNode } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { useAuthStore } from "@/stores/auth-store";
import { useWorkspaceStore } from "@/stores/workspace-store";

import { useAgentRuntimeSummariesStream } from "@/hooks/use-agent-runtime-summaries";

class StubWebSocket {
  static instances: StubWebSocket[] = [];
  url: string;
  closed = false;
  listeners: Record<string, ((event: MessageEvent) => void)[]> = {};
  constructor(url: string) {
    this.url = url;
    StubWebSocket.instances.push(this);
  }
  addEventListener(type: string, cb: (event: MessageEvent) => void): void {
    (this.listeners[type] ??= []).push(cb);
  }
  close(): void {
    this.closed = true;
  }
}

const originalWebSocket = globalThis.WebSocket;

function wrapper(client: QueryClient) {
  return function Wrapper({ children }: { children: ReactNode }) {
    return (
      <QueryClientProvider client={client}>{children}</QueryClientProvider>
    );
  };
}

describe("useAgentRuntimeSummariesStream", () => {
  beforeEach(() => {
    StubWebSocket.instances = [];
    // reason: jsdom does not provide a WebSocket constructor we can spy on.
    (globalThis as unknown as { WebSocket: typeof WebSocket }).WebSocket =
      StubWebSocket as unknown as typeof WebSocket;
  });

  afterEach(() => {
    (globalThis as unknown as { WebSocket: typeof WebSocket }).WebSocket =
      originalWebSocket;
    vi.restoreAllMocks();
  });

  it("noops when there is no auth token", () => {
    useAuthStore.setState({
      accessToken: null,
      accessExpiresAt: null,
      setAccess: useAuthStore.getState().setAccess,
      clear: useAuthStore.getState().clear,
    });
    useWorkspaceStore.setState({
      workspaces: [
        { id: "ws-1", name: "One", slug: "one" },
        { id: "ws-2", name: "Two", slug: "two" },
      ],
      activeWorkspaceId: "ws-1",
      setWorkspaces: useWorkspaceStore.getState().setWorkspaces,
      setActive: useWorkspaceStore.getState().setActive,
      clear: useWorkspaceStore.getState().clear,
    });
    const queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });
    renderHook(() => useAgentRuntimeSummariesStream(), {
      wrapper: wrapper(queryClient),
    });
    expect(StubWebSocket.instances).toHaveLength(0);
  });

  it("noops when the workspace list is empty", () => {
    useAuthStore.setState({
      accessToken: "test-token",
      accessExpiresAt: null,
      setAccess: useAuthStore.getState().setAccess,
      clear: useAuthStore.getState().clear,
    });
    useWorkspaceStore.setState({
      workspaces: [],
      activeWorkspaceId: null,
      setWorkspaces: useWorkspaceStore.getState().setWorkspaces,
      setActive: useWorkspaceStore.getState().setActive,
      clear: useWorkspaceStore.getState().clear,
    });
    const queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });
    renderHook(() => useAgentRuntimeSummariesStream(), {
      wrapper: wrapper(queryClient),
    });
    expect(StubWebSocket.instances).toHaveLength(0);
  });

  it("opens one socket multiplexing all workspace ids", () => {
    useAuthStore.setState({
      accessToken: "test-token",
      accessExpiresAt: null,
      setAccess: useAuthStore.getState().setAccess,
      clear: useAuthStore.getState().clear,
    });
    useWorkspaceStore.setState({
      workspaces: [
        { id: "ws-1", name: "One", slug: "one" },
        { id: "ws-2", name: "Two", slug: "two" },
      ],
      activeWorkspaceId: "ws-1",
      setWorkspaces: useWorkspaceStore.getState().setWorkspaces,
      setActive: useWorkspaceStore.getState().setActive,
      clear: useWorkspaceStore.getState().clear,
    });
    const queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });
    renderHook(() => useAgentRuntimeSummariesStream(), {
      wrapper: wrapper(queryClient),
    });
    expect(StubWebSocket.instances).toHaveLength(1);
    const url = StubWebSocket.instances[0]!.url;
    expect(url).toContain("/api/v1/agent-runtime/ws");
    expect(url).toContain("token=test-token");
    expect(url).toMatch(/subscribe_workspaces=ws-1%2Cws-2|subscribe_workspaces=ws-1,ws-2/);
  });
});
