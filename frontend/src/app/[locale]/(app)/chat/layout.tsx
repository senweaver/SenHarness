"use client";

import { useEffect, useRef } from "react";
import { useParams, useSearchParams } from "next/navigation";
import {
  type ImperativePanelHandle,
  Panel,
  PanelGroup,
  PanelResizeHandle,
} from "react-resizable-panels";

import { ChatHeader } from "@/components/chat/ChatHeader";
import { SessionList } from "@/components/chat/SessionList";
import { WorkspacePanel } from "@/components/workspace/WorkspacePanel";
import { decideApprovalFor } from "@/hooks/use-session-control";
import { usePermissions } from "@/hooks/use-permissions";
import { cn } from "@/lib/utils";
import { useSidebarStore } from "@/stores/sidebar-store";
import { useWorkspacePaneStore } from "@/stores/workspace-pane-store";

/**
 * Chat shell — three resizable panes:
 *
 *   ┌───────────────┬───────────────────────────────┬───────────────────┐
 *   │ SessionList   │ ChatHeader (sticky)           │ WorkspacePanel    │
 *   │  ~280 px      │ ───────────────────────────── │  collapsed 0 px   │
 *   │  (resizable)  │ Conversation + Composer       │  open ~520 px     │
 *   └───────────────┴───────────────────────────────┴───────────────────┘
 *
 * The ChatHeader lives at the top of the **middle** column (not the right
 * rail) so the title, status dot, share and workspace toggle stay aligned
 * with the chat content even when the conversation scrolls. When the user
 * collapses the workspace pane it shrinks to 0 px — there is no leftover
 * 40 px icon column biting into the chat width; the toggle on the
 * ChatHeader is the one and only re-expand affordance.
 *
 * Owning the WorkspacePanel at the layout level (rather than the per-route
 * page) means the right rail stays mounted across ``/chat``, ``/chat/new``
 * and ``/chat/[id]`` transitions. Approval / file state always lands in
 * the same place; users keep their preferred expanded width via
 * ``autoSaveId``.
 *
 * The session-bound chat page is the one that opens the WebSocket (via the
 * AI SDK transport). It binds the live socket through ``useSessionControl``
 * — that hook publishes the socket into a module-level registry so this
 * layout's ``decideApprovalFor`` can ack decisions without React context.
 */
export default function ChatLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  const params = useParams<{ sessionId?: string }>();
  const search = useSearchParams();
  const sessionId = params?.sessionId ?? null;
  // The /chat/new draft surface still wants an agent-aware popover; we
  // surface ``?agent=`` so ChatHeader can fall back to the agent identity
  // before the backing session exists.
  const draftAgentId = !sessionId ? (search?.get("agent") ?? null) : null;
  const collapsed = useWorkspacePaneStore((s) => s.collapsed);
  const setCollapsed = useWorkspacePaneStore((s) => s.setCollapsed);
  const sessionListCollapsed = useSidebarStore(
    (s) => s.chatSessionListCollapsed,
  );
  const setSessionListCollapsed = useSidebarStore(
    (s) => s.setChatSessionListCollapsed,
  );
  const perms = usePermissions();

  const canDecide = perms.canDecideApproval({
    requestedByIdentityId: perms.identityId,
    sessionOwnerIdentityId: perms.identityId,
    sessionOwnerDepartmentId: perms.departmentId,
  });

  // ─── Drive the resizable panel size from the workspace store ───
  // The ChatHeader toggle flips ``collapsed`` in zustand.
  // ``react-resizable-panels`` controls the pane width via inline
  // ``flex-basis``, so we use the imperative handle to call
  // ``collapse() / expand()`` whenever the store flag changes.
  const workspaceRef = useRef<ImperativePanelHandle>(null);
  useEffect(() => {
    const panel = workspaceRef.current;
    if (!panel) return;
    if (collapsed && !panel.isCollapsed()) panel.collapse();
    else if (!collapsed && panel.isCollapsed()) panel.expand();
  }, [collapsed]);

  const sessionListRef = useRef<ImperativePanelHandle>(null);
  useEffect(() => {
    const panel = sessionListRef.current;
    if (!panel) return;
    if (sessionListCollapsed && !panel.isCollapsed()) panel.collapse();
    else if (!sessionListCollapsed && panel.isCollapsed()) panel.expand();
  }, [sessionListCollapsed]);

  return (
    <div className="flex h-full min-h-0 flex-1 overflow-hidden">
      <PanelGroup
        direction="horizontal"
        autoSaveId="senharness.chat-shell"
      >
        <Panel
          ref={sessionListRef}
          id="senharness-chat-sessionlist"
          order={1}
          collapsible
          collapsedSize={0}
          defaultSize={sessionListCollapsed ? 0 : 18}
          minSize={14}
          maxSize={32}
          onCollapse={() => setSessionListCollapsed(true)}
          onExpand={() => setSessionListCollapsed(false)}
        >
          <SessionList />
        </Panel>
        <PanelResizeHandle
          className={cn(
            "w-px bg-border data-[resize-handle-state=hover]:bg-[rgb(var(--color-primary))]/50 data-[resize-handle-state=drag]:bg-[rgb(var(--color-primary))]",
            sessionListCollapsed && "pointer-events-none bg-transparent",
          )}
        />
        <Panel order={2} defaultSize={50} className="min-w-0">
          <div className="flex h-full min-h-0 flex-1 flex-col overflow-hidden">
            <ChatHeader sessionId={sessionId} agentId={draftAgentId} />
            <div className="flex min-h-0 flex-1 flex-col overflow-hidden">
              {children}
            </div>
          </div>
        </Panel>
        {/* When the workspace is collapsed the panel shrinks to 0 px, so
            the resize handle would have nothing to drag against. We keep
            the element mounted (so resizable-panels can re-attach when
            the user expands again) but neutralise its hit-area + visible
            line — the result is a seamless chat surface with no leftover
            seam. */}
        <PanelResizeHandle
          className={cn(
            "w-px bg-border data-[resize-handle-state=hover]:bg-[rgb(var(--color-primary))]/50",
            collapsed && "pointer-events-none bg-transparent",
          )}
        />
        <Panel
          ref={workspaceRef}
          id="senharness-chat-workspace"
          order={3}
          defaultSize={collapsed ? 0 : 32}
          minSize={22}
          maxSize={55}
          collapsible
          collapsedSize={0}
          // Sync the imperative collapse/expand back into the store so a
          // user-driven drag onto the rail collapses the panel cleanly.
          onCollapse={() => setCollapsed(true)}
          onExpand={() => setCollapsed(false)}
        >
          <WorkspacePanel
            sessionId={sessionId}
            decideApproval={(id, action) =>
              decideApprovalFor(sessionId, id, action)
            }
            canDecideApproval={canDecide}
          />
        </Panel>
      </PanelGroup>
    </div>
  );
}
