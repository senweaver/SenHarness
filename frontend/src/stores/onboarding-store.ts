import { create } from "zustand";

export type OnboardingStep = 1 | 2 | 3 | 4 | 5;

export interface OnboardingDraft {
  workspaceName?: string;
  workspaceDescription?: string;
  providerId?: string;
  agentId?: string;
}

interface PersistedShape {
  step: OnboardingStep;
  draft: OnboardingDraft;
}

const STORAGE_KEY = "senharness:onboarding";

function readPersisted(): PersistedShape | null {
  if (typeof window === "undefined") return null;
  try {
    const raw = window.sessionStorage.getItem(STORAGE_KEY);
    if (!raw) return null;
    const parsed = JSON.parse(raw) as PersistedShape;
    if (
      parsed &&
      typeof parsed === "object" &&
      typeof parsed.step === "number" &&
      parsed.step >= 1 &&
      parsed.step <= 5
    ) {
      return { step: parsed.step as OnboardingStep, draft: parsed.draft ?? {} };
    }
    return null;
  } catch {
    return null;
  }
}

function writePersisted(payload: PersistedShape): void {
  if (typeof window === "undefined") return;
  try {
    window.sessionStorage.setItem(STORAGE_KEY, JSON.stringify(payload));
  } catch {
    // ignore
  }
}

function clearPersisted(): void {
  if (typeof window === "undefined") return;
  try {
    window.sessionStorage.removeItem(STORAGE_KEY);
  } catch {
    // ignore
  }
}

interface OnboardingState {
  open: boolean;
  hydrated: boolean;
  step: OnboardingStep;
  draft: OnboardingDraft;
  hydrate: () => void;
  restart: () => void;
  start: () => void;
  close: (options?: { clear?: boolean }) => void;
  next: () => void;
  back: () => void;
  goTo: (step: OnboardingStep) => void;
  setDraft: (patch: Partial<OnboardingDraft>) => void;
}

export const useOnboardingStore = create<OnboardingState>((set, get) => ({
  open: false,
  hydrated: false,
  step: 1,
  draft: {},
  hydrate: () => {
    if (get().hydrated) return;
    const restored = readPersisted();
    if (restored) {
      set({
        hydrated: true,
        step: restored.step,
        draft: restored.draft,
      });
    } else {
      set({ hydrated: true });
    }
  },
  restart: () => {
    clearPersisted();
    set({ open: true, step: 1, draft: {} });
    writePersisted({ step: 1, draft: {} });
  },
  start: () => {
    const { step, draft } = get();
    set({ open: true });
    writePersisted({ step, draft });
  },
  close: ({ clear } = {}) => {
    if (clear) {
      clearPersisted();
      set({ open: false, step: 1, draft: {} });
    } else {
      set({ open: false });
    }
  },
  next: () => {
    const nextStep = Math.min(get().step + 1, 5) as OnboardingStep;
    const draft = get().draft;
    set({ step: nextStep });
    writePersisted({ step: nextStep, draft });
  },
  back: () => {
    const prev = Math.max(get().step - 1, 1) as OnboardingStep;
    const draft = get().draft;
    set({ step: prev });
    writePersisted({ step: prev, draft });
  },
  goTo: (step) => {
    const draft = get().draft;
    set({ step });
    writePersisted({ step, draft });
  },
  setDraft: (patch) => {
    const draft = { ...get().draft, ...patch };
    set({ draft });
    writePersisted({ step: get().step, draft });
  },
}));
