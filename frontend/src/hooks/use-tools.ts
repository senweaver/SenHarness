"use client";

import { useQuery } from "@tanstack/react-query";

import { api } from "@/lib/api";
import { useAuthStore } from "@/stores/auth-store";

export type ToolCategory =
  | "utility"
  | "web"
  | "filesystem"
  | "memory"
  | "multimedia"
  | "coding";

export interface ToolRegistryRow {
  name: string;
  description: string;
  category: ToolCategory;
  default_in: Array<"default" | "coding">;
}

/** Pull the user-selectable subset of ``BUILTIN_TOOL_REGISTRY``. */
export function useToolRegistry() {
  const token = useAuthStore((s) => s.accessToken);
  return useQuery<ToolRegistryRow[]>({
    queryKey: ["tools", "registry"],
    queryFn: () => api.get<ToolRegistryRow[]>("/api/v1/tools/registry"),
    enabled: Boolean(token),
    staleTime: 60 * 60_000,
  });
}
