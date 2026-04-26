"use client";

import { QueryClient } from "@tanstack/react-query";

let client: QueryClient | null = null;

export function getQueryClient(): QueryClient {
  if (!client) {
    client = new QueryClient({
      defaultOptions: {
        queries: {
          staleTime: 30 * 1000,
          refetchOnWindowFocus: false,
          retry: (failureCount, error: unknown) => {
            if ((error as { status?: number })?.status === 401) return false;
            return failureCount < 2;
          },
        },
      },
    });
  }
  return client;
}
