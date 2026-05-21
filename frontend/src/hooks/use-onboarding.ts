"use client";

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { api } from "@/lib/api";
import type { OnboardingCompleteOut } from "@/types/api";

export function useCompleteOnboarding() {
  const queryClient = useQueryClient();
  return useMutation<OnboardingCompleteOut>({
    mutationFn: () =>
      api.post<OnboardingCompleteOut>("/api/v1/onboarding/complete", {}),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["me"] });
    },
  });
}
