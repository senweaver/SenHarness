"use client";

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { api } from "@/lib/api";
import type { SessionRead } from "@/types/api";

export interface CreateSessionInput {
  kind?: "p2p" | "squad" | "channel";
  subject_id?: string | null;
  title?: string | null;
}

export function useCreateSession() {
  const qc = useQueryClient();
  return useMutation<SessionRead, unknown, CreateSessionInput>({
    mutationFn: (input) =>
      api.post<SessionRead>("/api/v1/sessions", {
        kind: input.kind ?? "p2p",
        subject_id: input.subject_id ?? null,
        title: input.title ?? null,
      }),
    onSuccess: (created) => {
      // Optimistically prepend to every cached ``sessions/recent`` list
      // (the key carries workspace + limit so we update each variant in
      // place). Without this the new conversation lives at the bottom
      // until the next list refetch.
      qc.setQueriesData<SessionRead[]>(
        { queryKey: ["sessions", "recent"] },
        (current) => {
          if (!Array.isArray(current)) return current;
          if (current.some((s) => s.id === created.id)) return current;
          return [created, ...current];
        },
      );
      qc.invalidateQueries({ queryKey: ["sessions", "recent"] });
    },
  });
}
