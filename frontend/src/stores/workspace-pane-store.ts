"use client";

import { create } from "zustand";
import { persist } from "zustand/middleware";

/**
 * Right-rail "Workspace Panel" store.
 *
 * The chat surface keeps a single source of truth for which side-panel tab is
 * open and what context the panel should focus on. Components that *trigger*
 * panel state changes (e.g. an Artifacts auto-open when an ``edit_file`` tool
 * fires, an Approvals auto-open when ``approval_request`` arrives) write to
 * this store; the panel container reads from it.
 *
 * Persistence: the collapsed state is persisted so users land in the same
 * layout next session. Active tab is *not* persisted because routing across
 * sessions can render the previous tab's context stale.
 */
export type WorkspaceTab =
  | "trace"
  | "plan"
  | "files"
  | "sources"
  | "memory"
  | "approvals"
  | "terminal";

const TAB_VALUES: readonly WorkspaceTab[] = [
  "trace",
  "plan",
  "files",
  "sources",
  "memory",
  "approvals",
  "terminal",
];

/** Coerce a possibly-stale persisted tab id into a known one. Older builds
 *  shipped ``artifacts`` and ``settings`` — once a tenant upgrades we silently
 *  redirect to the closest match (``files`` for old artifacts, ``trace`` for
 *  the dropped settings tab) instead of leaving the panel in an unknown
 *  state.*/
function coerceTab(raw: unknown): WorkspaceTab {
  if (typeof raw !== "string") return "trace";
  if ((TAB_VALUES as readonly string[]).includes(raw)) return raw as WorkspaceTab;
  if (raw === "artifacts") return "files";
  return "trace";
}

export interface WorkspaceContext {
  /** When focusing on a specific tool call (Trace tab anchor). */
  toolCallId?: string;
  /** When focusing on a specific artifact (Artifacts tab anchor). */
  artifactId?: string;
  /** Approval id to scroll to. */
  approvalId?: string;
}

interface WorkspacePaneState {
  collapsed: boolean;
  activeTab: WorkspaceTab;
  context: WorkspaceContext;
  /** Toggle the collapsed rail. */
  toggleCollapsed: () => void;
  setCollapsed: (v: boolean) => void;
  /** Force the panel open and switch to a tab (with optional anchor context). */
  openTab: (tab: WorkspaceTab, context?: WorkspaceContext) => void;
  /** Switch tab without changing the collapsed state. */
  setActiveTab: (tab: WorkspaceTab) => void;
  /** Replace the focus anchor (artifact / tool call / approval). */
  setContext: (context: WorkspaceContext) => void;
  /** Clear the focus anchor (e.g. after navigating between sessions). */
  resetContext: () => void;
}

export const useWorkspacePaneStore = create<WorkspacePaneState>()(
  persist(
    (set) => ({
      collapsed: true,
      activeTab: "trace",
      context: {},
      toggleCollapsed: () => set((s) => ({ collapsed: !s.collapsed })),
      setCollapsed: (v) => set({ collapsed: v }),
      openTab: (tab, context) =>
        set({
          collapsed: false,
          activeTab: coerceTab(tab),
          context: context ?? {},
        }),
      setActiveTab: (tab) => set({ activeTab: coerceTab(tab) }),
      setContext: (context) => set({ context }),
      resetContext: () => set({ context: {} }),
    }),
    {
      name: "senharness.workspace-pane",
      // Only persist the collapsed flag; everything else is per-session.
      partialize: (state) => ({ collapsed: state.collapsed }),
    },
  ),
);
