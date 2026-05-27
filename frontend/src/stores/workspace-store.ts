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
  /**
   * Identity the persisted workspace state belongs to. When ``useMe``
   * reports a different identity (e.g. account switch in the same
   * browser, OAuth login after a previous user signed out) the store
   * is cleared before re-binding so the previous account's
   * ``activeWorkspaceId`` cannot leak into the new session.
   */
  boundIdentityId: string | null;
  setWorkspaces: (list: WorkspaceBrief[]) => void;
  setActive: (id: string | null) => void;
  bindIdentity: (identityId: string) => void;
  clear: () => void;
}

export const useWorkspaceStore = create<WorkspaceState>()(
  persist(
    (set, get) => ({
      workspaces: [],
      activeWorkspaceId: null,
      boundIdentityId: null,
      setWorkspaces: (list) => set({ workspaces: list }),
      setActive: (id) => set({ activeWorkspaceId: id }),
      bindIdentity: (identityId) => {
        const current = get().boundIdentityId;
        if (current && current !== identityId) {
          set({
            workspaces: [],
            activeWorkspaceId: null,
            boundIdentityId: identityId,
          });
          return;
        }
        if (!current) {
          set({ boundIdentityId: identityId });
        }
      },
      clear: () =>
        set({
          workspaces: [],
          activeWorkspaceId: null,
          boundIdentityId: null,
        }),
    }),
    { name: "senharness.workspace" },
  ),
);
