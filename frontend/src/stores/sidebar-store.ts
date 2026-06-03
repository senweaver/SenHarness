import { create } from "zustand";
import { persist } from "zustand/middleware";

interface SidebarState {
  collapsed: boolean;
  mySectionOpen: boolean;
  workspaceSectionOpen: boolean;
  chatSessionListCollapsed: boolean;
  preChatCollapsed: boolean | null;
  toggleCollapsed: () => void;
  setCollapsed: (v: boolean) => void;
  setMySectionOpen: (v: boolean) => void;
  toggleMySectionOpen: () => void;
  setWorkspaceSectionOpen: (v: boolean) => void;
  toggleWorkspaceSectionOpen: () => void;
  setChatSessionListCollapsed: (v: boolean) => void;
  toggleChatSessionListCollapsed: () => void;
  setPreChatCollapsed: (v: boolean | null) => void;
}

export const useSidebarStore = create<SidebarState>()(
  persist(
    (set) => ({
      collapsed: false,
      mySectionOpen: true,
      workspaceSectionOpen: true,
      chatSessionListCollapsed: false,
      preChatCollapsed: null,
      toggleCollapsed: () => set((s) => ({ collapsed: !s.collapsed })),
      setCollapsed: (v) => set({ collapsed: v }),
      setMySectionOpen: (v) => set({ mySectionOpen: v }),
      toggleMySectionOpen: () =>
        set((s) => ({ mySectionOpen: !s.mySectionOpen })),
      setWorkspaceSectionOpen: (v) => set({ workspaceSectionOpen: v }),
      toggleWorkspaceSectionOpen: () =>
        set((s) => ({ workspaceSectionOpen: !s.workspaceSectionOpen })),
      setChatSessionListCollapsed: (v) =>
        set({ chatSessionListCollapsed: v }),
      toggleChatSessionListCollapsed: () =>
        set((s) => ({ chatSessionListCollapsed: !s.chatSessionListCollapsed })),
      setPreChatCollapsed: (v) => set({ preChatCollapsed: v }),
    }),
    {
      name: "senharness.sidebar",
      version: 2,
      migrate: (persisted, fromVersion) => {
        const state = persisted as Partial<SidebarState>;
        if (fromVersion < 2) {
          return { ...state, workspaceSectionOpen: true };
        }
        return state;
      },
      partialize: (state) => ({
        collapsed: state.collapsed,
        mySectionOpen: state.mySectionOpen,
        workspaceSectionOpen: state.workspaceSectionOpen,
        chatSessionListCollapsed: state.chatSessionListCollapsed,
      }),
    },
  ),
);
