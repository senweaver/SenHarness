"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api } from "@/lib/api";

// ─── Types ─────────────────────────────────────────────────────
export interface RuntimeCapabilities {
  supports_streaming: boolean;
  supports_parallel_tools: boolean;
  supports_thinking: boolean;
  supports_native_mcp: boolean;
  supports_vision: boolean;
  max_context_tokens: number | null;
  notes: string;
}

export interface RegisteredRuntime {
  kind: string;
  display_name: string;
  description: string;
  docs_url: string;
  requires_adapter: boolean;
  capabilities: RuntimeCapabilities;
}

export interface RuntimeSwitchInput {
  backend_kind: string;
  backend_adapter_id?: string | null;
  note?: string | null;
}

export interface RuntimeSwitchResult {
  agent_id: string;
  backend_kind: string;
  backend_adapter_id: string | null;
  switched_from: string;
}

export interface RuntimeCompareInput {
  prompt: string;
  runtimes: string[];
  include_eval?: boolean;
}

export interface RuntimeCompareVerdict {
  score: number;
  verdict: "pass" | "warn" | "fail";
  reasons: string[];
  aux_model: string | null;
  nli_agreement: number | null;
}

export interface RuntimeCompareCandidate {
  runtime: string;
  ok: boolean;
  latency_ms: number;
  tokens: { input?: number; output?: number };
  cost_usd: number;
  final_text: string | null;
  error: string | null;
  verdict: RuntimeCompareVerdict | null;
}

export interface RuntimeCompareResult {
  agent_id: string;
  prompt: string;
  candidates: RuntimeCompareCandidate[];
}

// ─── Hooks ─────────────────────────────────────────────────────
const KEY = ["runtimes"] as const;

/** List every registered Agent Runtime (`native`, `openclaw`, ...). */
export function useRegisteredRuntimes() {
  return useQuery<RegisteredRuntime[]>({
    queryKey: KEY,
    queryFn: async () => {
      const payload = await api.get<
        | RegisteredRuntime[]
        | { runtimes?: RegisteredRuntime[]; count?: number }
      >("/api/v1/agents/runtimes");
      if (Array.isArray(payload)) return payload;
      return payload.runtimes ?? [];
    },
    staleTime: 60_000,
  });
}

/** Switch an Agent's runtime without rebuilding it. */
export function useSwitchRuntime(agentId: string) {
  const qc = useQueryClient();
  return useMutation<RuntimeSwitchResult, unknown, RuntimeSwitchInput>({
    mutationFn: (body) =>
      api.post<RuntimeSwitchResult>(
        `/api/v1/agents/${agentId}/runtime/switch`,
        body,
      ),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: KEY });
      qc.invalidateQueries({ queryKey: ["agent", agentId] });
    },
  });
}

/** Run the same prompt against N runtimes and return side-by-side metrics. */
export function useCompareRuntimes(agentId: string) {
  return useMutation<RuntimeCompareResult, unknown, RuntimeCompareInput>({
    mutationFn: (body) =>
      api.post<RuntimeCompareResult>(
        `/api/v1/agents/${agentId}/runtime/compare`,
        body,
      ),
  });
}
