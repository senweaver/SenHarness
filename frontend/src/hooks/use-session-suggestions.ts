"use client";

import { useMutation } from "@tanstack/react-query";

import { api } from "@/lib/api";

/**
 * Calls ``POST /api/v1/sessions/{id}/suggestions`` to generate 3-5 follow-up
 * question chips for the chat surface. Implemented as a mutation rather
 * than a query so the caller controls *when* the (paid) LLM round-trip
 * fires — typically right after a streaming reply finalises.
 *
 * The endpoint silently returns an empty array when no model is configured
 * or the LLM call fails — the chat surface should treat ``[]`` as "no
 * suggestions" without surfacing an error toast.
 */
export function useSessionSuggestions() {
  return useMutation<string[], Error, string>({
    mutationFn: (sessionId: string) =>
      api.post<string[]>(`/api/v1/sessions/${sessionId}/suggestions`, {}),
  });
}
