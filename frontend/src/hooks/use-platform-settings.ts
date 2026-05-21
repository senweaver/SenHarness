"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api } from "@/lib/api";
import { useAuthStore } from "@/stores/auth-store";
import type {
  PlatformSettingsListOut,
  PlatformSettingSection,
  PlatformSettingsResetOut,
  PlatformSettingsSchema,
  PlatformSmtpTestIn,
  PlatformSmtpTestOut,
  PlatformOAuthTestOut,
} from "@/types/api";

const BASE = "/api/v1/admin/platform-settings";

export function usePlatformSections() {
  const token = useAuthStore((s) => s.accessToken);
  return useQuery<PlatformSettingsListOut>({
    queryKey: ["platform-settings", "list"],
    queryFn: () => api.get<PlatformSettingsListOut>(BASE),
    enabled: Boolean(token),
    staleTime: 30_000,
  });
}

export function usePlatformSection(section: string | null) {
  const token = useAuthStore((s) => s.accessToken);
  return useQuery<PlatformSettingSection>({
    queryKey: ["platform-settings", "section", section],
    queryFn: () =>
      api.get<PlatformSettingSection>(`${BASE}/${encodeURIComponent(section!)}`),
    enabled: Boolean(token) && Boolean(section),
    staleTime: 30_000,
  });
}

export function usePlatformSectionSchema(section: string | null) {
  const token = useAuthStore((s) => s.accessToken);
  return useQuery<PlatformSettingsSchema>({
    queryKey: ["platform-settings", "schema", section],
    queryFn: () =>
      api.get<PlatformSettingsSchema>(
        `${BASE}/${encodeURIComponent(section!)}/schema`,
      ),
    enabled: Boolean(token) && Boolean(section),
    staleTime: 5 * 60_000,
  });
}

export function useUpdatePlatformSection() {
  const qc = useQueryClient();
  return useMutation<
    PlatformSettingSection,
    unknown,
    {
      section: string;
      value: Record<string, unknown>;
      confirmed_dangerous?: boolean;
    }
  >({
    mutationFn: ({ section, value, confirmed_dangerous }) =>
      api.put<PlatformSettingSection>(
        `${BASE}/${encodeURIComponent(section)}`,
        { value, confirmed_dangerous: confirmed_dangerous ?? false },
      ),
    onSuccess: (_data, vars) => {
      qc.invalidateQueries({ queryKey: ["platform-settings", "list"] });
      qc.invalidateQueries({
        queryKey: ["platform-settings", "section", vars.section],
      });
    },
  });
}

export function useResetPlatformSection() {
  const qc = useQueryClient();
  return useMutation<PlatformSettingsResetOut, unknown, { section: string }>({
    mutationFn: ({ section }) =>
      api.post<PlatformSettingsResetOut>(
        `${BASE}/${encodeURIComponent(section)}/reset`,
      ),
    onSuccess: (_data, vars) => {
      qc.invalidateQueries({ queryKey: ["platform-settings", "list"] });
      qc.invalidateQueries({
        queryKey: ["platform-settings", "section", vars.section],
      });
    },
  });
}

export function useTestSmtp() {
  return useMutation<PlatformSmtpTestOut, unknown, PlatformSmtpTestIn>({
    mutationFn: (body) =>
      api.post<PlatformSmtpTestOut>(`${BASE}/email.smtp/test`, body),
  });
}

export function useTestOAuth() {
  return useMutation<PlatformOAuthTestOut, unknown, { provider: string }>({
    mutationFn: ({ provider }) =>
      api.post<PlatformOAuthTestOut>(
        `${BASE}/auth.oauth/${encodeURIComponent(provider)}/test`,
      ),
  });
}
