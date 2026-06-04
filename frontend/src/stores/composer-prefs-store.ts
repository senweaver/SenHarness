import { create } from "zustand";
import { persist } from "zustand/middleware";

import type { ChatMode } from "@/lib/ws";

/**
 * Persisted composer preferences shared across chat surfaces.
 *
 * ``mode`` lives here (instead of local ``ChatInput`` state) so the
 * choice survives the ``/chat/new`` → ``/chat/[id]`` remount: a fresh
 * composer restores the user's last pick instead of snapping back to
 * ``flash``.
 */
interface ComposerPrefsState {
  mode: ChatMode;
  setMode: (mode: ChatMode) => void;
}

export const useComposerPrefsStore = create<ComposerPrefsState>()(
  persist(
    (set) => ({
      mode: "flash",
      setMode: (mode) => set({ mode }),
    }),
    {
      name: "senharness.composer-prefs",
      partialize: (state) => ({ mode: state.mode }),
    },
  ),
);
