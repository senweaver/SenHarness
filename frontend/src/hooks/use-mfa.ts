"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "@/lib/api";
import { useAuthStore } from "@/stores/auth-store";

export interface MfaStatus {
  enabled: boolean;
  pending: boolean;
}

export interface MfaSetupOut {
  otpauth_uri: string;
  secret: string;
}

/**
 * Poll the current user's MFA status. Cheap endpoint — we still throttle with
 * `staleTime` so the profile page doesn't re-fetch on every tab focus.
 */
export function useMfaStatus() {
  const tok = useAuthStore((s) => s.accessToken);
  return useQuery<MfaStatus>({
    queryKey: ["mfa", "status"],
    queryFn: () => api.get<MfaStatus>("/api/v1/auth/mfa/status"),
    enabled: Boolean(tok),
    staleTime: 30_000,
  });
}

export function useMfaSetup() {
  const qc = useQueryClient();
  return useMutation<MfaSetupOut, unknown, void>({
    mutationFn: () => api.post<MfaSetupOut>("/api/v1/auth/mfa/setup", {}),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["mfa", "status"] }),
  });
}

export function useMfaActivate() {
  const qc = useQueryClient();
  return useMutation<void, unknown, { code: string }>({
    mutationFn: ({ code }) => api.post("/api/v1/auth/mfa/activate", { code }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["mfa", "status"] }),
  });
}

export function useMfaDisable() {
  const qc = useQueryClient();
  return useMutation<void, unknown, { password: string }>({
    mutationFn: ({ password }) =>
      api.post("/api/v1/auth/mfa/disable", { password }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["mfa", "status"] }),
  });
}

export function useOAuthProviders() {
  return useQuery<{ providers: string[] }>({
    queryKey: ["auth", "oauth_providers"],
    queryFn: () => api.get<{ providers: string[] }>("/api/v1/auth/oauth/providers"),
    staleTime: 5 * 60_000,
  });
}
