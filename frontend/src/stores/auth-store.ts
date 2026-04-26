import { create } from "zustand";
import { persist } from "zustand/middleware";

interface AuthState {
  accessToken: string | null;
  accessExpiresAt: string | null;
  setAccess: (token: string, expiresAt: string) => void;
  clear: () => void;
}

export const useAuthStore = create<AuthState>()(
  persist(
    (set) => ({
      accessToken: null,
      accessExpiresAt: null,
      setAccess: (token, expiresAt) => set({ accessToken: token, accessExpiresAt: expiresAt }),
      clear: () => set({ accessToken: null, accessExpiresAt: null }),
    }),
    { name: "senharness.auth" },
  ),
);
