"use client";

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { api } from "@/lib/api";
import type { MeOut } from "@/types/api";

export interface UpdateMeInput {
  name?: string;
  avatar_url?: string | null;
  profile_json?: Record<string, unknown>;
}

export function useUpdateMe() {
  const qc = useQueryClient();
  return useMutation<MeOut, unknown, UpdateMeInput>({
    mutationFn: (input) => api.patch<MeOut>("/api/v1/me", input),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["me"] });
    },
  });
}

export function useChangePassword() {
  return useMutation<void, unknown, { old_password: string; new_password: string }>(
    {
      mutationFn: (input) => api.post("/api/v1/me/password", input),
    },
  );
}
