import { create } from "zustand";
import { persist } from "zustand/middleware";

/**
 * Per-user session pin preference. Backend has no per-identity pin column,
 * so this is intentionally client-only (persisted to localStorage). Pinning
 * order is independent of last-message bucketing — pinned items always
 * appear at the top in the SessionList sidebar.
 */
interface SessionPinState {
  pinned: Record<string, boolean>;
  toggle: (sessionId: string) => void;
  isPinned: (sessionId: string) => boolean;
  remove: (sessionId: string) => void;
}

export const useSessionPinStore = create<SessionPinState>()(
  persist(
    (set, get) => ({
      pinned: {},
      toggle: (sessionId) =>
        set((s) => {
          const next = { ...s.pinned };
          if (next[sessionId]) delete next[sessionId];
          else next[sessionId] = true;
          return { pinned: next };
        }),
      isPinned: (sessionId) => Boolean(get().pinned[sessionId]),
      remove: (sessionId) =>
        set((s) => {
          if (!s.pinned[sessionId]) return s;
          const next = { ...s.pinned };
          delete next[sessionId];
          return { pinned: next };
        }),
    }),
    { name: "senharness:session-pins" },
  ),
);
