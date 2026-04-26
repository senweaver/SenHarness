import { create } from "zustand";

import type { AttachmentRef } from "@/components/chat/AttachmentView";

/**
 * Transient store carrying a just-composed prompt from Home → Chat.
 *
 * Lifecycle:
 *   HeroPrompt.submit → setPending(text, { attachments, webSearch }) →
 *                       router.push(/chat/{sid})
 *   ChatSession onMount → consume() → auto-send once
 */
export interface PendingPromptPayload {
  text: string;
  attachments?: AttachmentRef[];
  /** When true, the chat session prepends a `[web_search]` hint so the agent
   *  can pick up the user's intent to use web search on this turn. The prefix
   *  is purely a UX nudge — the actual web tool gating still happens via the
   *  agent's tool whitelist on the backend. */
  webSearch?: boolean;
}

interface PendingPromptState {
  pendingByStation: Record<string, PendingPromptPayload>;
  /** Set the pending payload. Accepts either a bare string (back-compat with
   *  the original API shape) or a full payload. */
  setPending: (
    sessionId: string,
    payload: string | PendingPromptPayload,
  ) => void;
  consume: (sessionId: string) => PendingPromptPayload | null;
}

export const usePendingPromptStore = create<PendingPromptState>((set, get) => ({
  pendingByStation: {},
  setPending: (sessionId, payload) =>
    set((s) => ({
      pendingByStation: {
        ...s.pendingByStation,
        [sessionId]:
          typeof payload === "string" ? { text: payload } : payload,
      },
    })),
  consume: (sessionId) => {
    const payload = get().pendingByStation[sessionId] ?? null;
    if (payload !== null) {
      set((s) => {
        const next = { ...s.pendingByStation };
        delete next[sessionId];
        return { pendingByStation: next };
      });
    }
    return payload;
  },
}));
