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
  identityId: string | null;
}

const STORAGE_KEY = "senharness:onboarding";

function readPersisted(): PersistedShape | null {
  if (typeof window === "undefined") return null;
  try {
    const raw = window.sessionStorage.getItem(STORAGE_KEY);
    if (!raw) return null;
    const parsed = JSON.parse(raw) as Partial<PersistedShape>;
    if (
      parsed &&
      typeof parsed === "object" &&
      typeof parsed.step === "number" &&
      parsed.step >= 1 &&
      parsed.step <= 5
    ) {
      return {
        step: parsed.step as OnboardingStep,
        draft: parsed.draft ?? {},
        identityId:
          typeof parsed.identityId === "string" ? parsed.identityId : null,
      };
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
  boundIdentityId: string | null;
  hydrate: () => void;
  restart: () => void;
  start: () => void;
  close: (options?: { clear?: boolean }) => void;
  next: () => void;
  back: () => void;
  goTo: (step: OnboardingStep) => void;
  setDraft: (patch: Partial<OnboardingDraft>) => void;
  bindIdentity: (identityId: string) => void;
}

function snapshot(state: {
  step: OnboardingStep;
  draft: OnboardingDraft;
  boundIdentityId: string | null;
}): PersistedShape {
  return {
    step: state.step,
    draft: state.draft,
    identityId: state.boundIdentityId,
  };
}

export const useOnboardingStore = create<OnboardingState>((set, get) => ({
  open: false,
  hydrated: false,
  step: 1,
  draft: {},
  boundIdentityId: null,
  hydrate: () => {
    if (get().hydrated) return;
    const restored = readPersisted();
    if (restored) {
      set({
        hydrated: true,
        step: restored.step,
        draft: restored.draft,
        boundIdentityId: restored.identityId,
      });
    } else {
      set({ hydrated: true });
    }
  },
  restart: () => {
    clearPersisted();
    const id = get().boundIdentityId;
    set({ open: true, step: 1, draft: {} });
    writePersisted({ step: 1, draft: {}, identityId: id });
  },
  start: () => {
    set({ open: true });
    writePersisted(snapshot(get()));
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
    set({ step: nextStep });
    writePersisted(snapshot(get()));
  },
  back: () => {
    const prev = Math.max(get().step - 1, 1) as OnboardingStep;
    set({ step: prev });
    writePersisted(snapshot(get()));
  },
  goTo: (step) => {
    set({ step });
    writePersisted(snapshot(get()));
  },
  setDraft: (patch) => {
    const draft = { ...get().draft, ...patch };
    set({ draft });
    writePersisted(snapshot(get()));
  },
  bindIdentity: (identityId) => {
    const current = get().boundIdentityId;
    if (current && current !== identityId) {
      clearPersisted();
      set({
        open: false,
        step: 1,
        draft: {},
        boundIdentityId: identityId,
      });
      return;
    }
    if (current !== identityId) {
      set({ boundIdentityId: identityId });
      writePersisted(snapshot(get()));
    }
  },
}));
