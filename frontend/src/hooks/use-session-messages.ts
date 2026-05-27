"use client";

import { useQuery } from "@tanstack/react-query";

import { api } from "@/lib/api";
import type { MessageRead } from "@/types/api";

export function useSessionMessages(sessionId: string | undefined, enabled = true) {
  return useQuery<MessageRead[]>({
    queryKey: ["sessions", "messages", sessionId],
    queryFn: () => api.get<MessageRead[]>(`/api/v1/sessions/${sessionId}/messages`),
    enabled: Boolean(sessionId) && enabled,
  });
}
