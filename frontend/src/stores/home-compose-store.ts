import { create } from "zustand";

import type { AttachmentRef } from "@/components/chat/AttachmentView";

/**
 * Shared compose state for the home page. Lets `QuickActions` (the chip
 * row underneath the hero composer) seed the draft/agent/tool flags
 * without prop drilling, while `HeroPrompt` owns the actual textarea.
 *
 * Cleared by `HeroPrompt.submit` once the message is dispatched.
 */
interface HomeComposeState {
  draft: string;
  setDraft: (v: string) => void;

  agentId: string | null;
  setAgentId: (v: string | null) => void;

  attachments: AttachmentRef[];
  addAttachment: (a: AttachmentRef) => void;
  removeAttachment: (id: string) => void;
  clearAttachments: () => void;

  webSearch: boolean;
  toggleWebSearch: () => void;

  /** When non-null, `HeroPrompt` consumes this on next render and focuses
   *  the textarea — used by QuickActions chips ("Write", "Image", "Video"). */
  starter: string | null;
  setStarter: (v: string | null) => void;

  reset: () => void;
}

export const useHomeComposeStore = create<HomeComposeState>((set) => ({
  draft: "",
  setDraft: (v) => set({ draft: v }),

  agentId: null,
  setAgentId: (v) => set({ agentId: v }),

  attachments: [],
  addAttachment: (a) =>
    set((s) =>
      s.attachments.some((x) => x.id === a.id)
        ? s
        : { attachments: [...s.attachments, a] },
    ),
  removeAttachment: (id) =>
    set((s) => ({ attachments: s.attachments.filter((a) => a.id !== id) })),
  clearAttachments: () => set({ attachments: [] }),

  webSearch: false,
  toggleWebSearch: () => set((s) => ({ webSearch: !s.webSearch })),

  starter: null,
  setStarter: (v) => set({ starter: v }),

  reset: () =>
    set({
      draft: "",
      attachments: [],
      webSearch: false,
      starter: null,
    }),
}));
