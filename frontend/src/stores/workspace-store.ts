import { create } from "zustand";
import { persist } from "zustand/middleware";

export interface WorkspaceBrief {
  id: string;
  name: string;
  slug: string;
  role?: string;
  branding?: {
    agent_term?: string;
    welcome_h1?: string;
    primary_color?: string;
    logo_url?: string | null;
  };
}

interface WorkspaceState {
  workspaces: WorkspaceBrief[];
  activeWorkspaceId: string | null;
  setWorkspaces: (list: WorkspaceBrief[]) => void;
  setActive: (id: string) => void;
  clear: () => void;
}

export const useWorkspaceStore = create<WorkspaceState>()(
  persist(
    (set) => ({
      workspaces: [],
      activeWorkspaceId: null,
      setWorkspaces: (list) => set({ workspaces: list }),
      setActive: (id) => set({ activeWorkspaceId: id }),
      clear: () => set({ workspaces: [], activeWorkspaceId: null }),
    }),
    { name: "senharness.workspace" },
  ),
);
