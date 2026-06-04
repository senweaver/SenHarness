"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "@/lib/api";
import { useAuthStore } from "@/stores/auth-store";
import { useWorkspaceStore } from "@/stores/workspace-store";

// ─── Types ─────────────────────────────────────────────────────────

export type ProviderKind = string;

export type CredentialType = "api_key" | "oauth_token" | "custom_headers";

export type ProviderFamily =
  | "openai-compatible"
  | "anthropic"
  | "google"
  | "bedrock"
  | "cohere"
  | "mistral"
  | "huggingface"
  | "outlines"
  | "embedding";

export interface ProviderRead {
  id: string;
  workspace_id: string;
  kind: ProviderKind;
  name: string;
  base_url: string | null;
  default_model: string | null;
  enabled: boolean;
  credential_type: CredentialType;
  country_code: string | null;
  metadata_json: Record<string, unknown>;
  has_key: boolean;
  /**
   * Last 4 characters of the stored API key (or ``null`` when no key
   * is configured / a legacy row predates the hint column). The full
   * plaintext key never leaves the backend — render this as
   * ``••••••${hint}`` next to the "Configured" badge.
   */
  api_key_hint: string | null;
  sort_order: number;
  created_at: string;
  updated_at: string;
}

export interface ProviderReorderRequest {
  ordered_ids: string[];
}

export interface ProviderCreate {
  kind: ProviderKind;
  name: string;
  base_url?: string | null;
  default_model?: string | null;
  api_key?: string | null;
  enabled?: boolean;
  credential_type?: CredentialType;
  country_code?: string | null;
  metadata_json?: Record<string, unknown>;
}

export type ProviderUpdate = Partial<ProviderCreate>;

export type ModelCategory =
  | "chat"
  | "image"
  | "video"
  | "embedding"
  | "asr"
  | "tts";

export interface ProviderCatalogModelStub {
  model: string;
  name: string;
  family: string;
  recommended: boolean;
  description: string;
  category?: ModelCategory;
  capabilities?: string[];
  context_window?: number | null;
  pricing?: [number, number] | null;
}

export interface ProviderCatalogEntry {
  kind: ProviderKind;
  display_name: string;
  display_name_zh: string;
  family: ProviderFamily;
  country_code: string | null;
  credential_type: CredentialType;
  description: string;
  description_zh: string;
  default_base_url: string | null;
  api_key_env: string | null;
  supports_discover: boolean;
  signup_url: string;
  aliases?: string[];
  builtin_models: ProviderCatalogModelStub[];
}

export function catalogKindForProvider(
  providerKind: string,
  catalog: ProviderCatalogEntry[],
): string {
  if (catalog.some((entry) => entry.kind === providerKind)) {
    return providerKind;
  }
  for (const entry of catalog) {
    if (entry.aliases?.includes(providerKind)) {
      return entry.kind;
    }
  }
  return providerKind;
}

export interface ProviderModelRead {
  id: string;
  provider_id: string;
  model: string;
  label: string | null;
  family: string | null;
  recommended: boolean;
  enabled: boolean;
  context_window: number | null;
  source: string;
  sort_order: number;
  metadata_json: Record<string, unknown>;
  created_at: string;
  updated_at: string;
}

export interface ProviderModelManualCreate {
  model: string;
  label?: string | null;
  family?: string | null;
  context_window?: number | null;
  enabled?: boolean;
}

export interface ProviderModelUpdate {
  enabled?: boolean | null;
  label?: string | null;
  recommended?: boolean | null;
  context_window?: number | null;
  capabilities?: string[] | null;
  sort_order?: number | null;
  /**
   * Sparse patch on the stored ``metadata_json`` dict. The backend
   * shallow-merges: passing ``{ profile: null }`` clears the per-row
   * reasoning override so the builtin profile takes over again.
   */
  metadata_json?: Record<string, unknown> | null;
}

export interface ProviderModelReorderRequest {
  ordered_ids: string[];
}

export interface DiscoveredModel {
  model: string;
  label: string | null;
  family: string | null;
  recommended: boolean;
  in_db: boolean;
  context_window: number | null;
  category?: ModelCategory;
  capabilities?: string[];
  pricing?: [number, number] | null;
}

export interface DiscoverResponse {
  kind: string;
  source: "remote" | "static";
  discovered: DiscoveredModel[];
  existing_ids: string[];
  error: string | null;
}

export interface DiscoverApplyRequest {
  model_ids: string[];
  replace?: boolean;
}

export interface ProviderTestResponse {
  ok: boolean;
  latency_ms: number | null;
  detail: string | null;
  error: string | null;
}

export type ReasoningEffort = "minimal" | "low" | "medium" | "high";

/**
 * Effective reasoning profile for a provider model — merge of the
 * builtin catalog default with the row's stored ``metadata_json``
 * override. Server-resolved so the operator dialog opens with the
 * values the runner actually applies.
 */
export interface ResolvedReasoningProfile {
  supported: boolean;
  hybrid: boolean;
  default: "on" | "off";
  tool_call_safe: boolean;
  supports_effort: boolean;
  source: "builtin" | "override" | "default";
  preferred_effort: ReasoningEffort | null;
  flash_alternative: string | null;
  has_db_override: boolean;
}

// ─── Provider list / CRUD ──────────────────────────────────────────

export function useProviders() {
  const tok = useAuthStore((s) => s.accessToken);
  const ws = useWorkspaceStore((s) => s.activeWorkspaceId);
  return useQuery<ProviderRead[]>({
    queryKey: ["providers", ws],
    queryFn: () => api.get<ProviderRead[]>("/api/v1/providers"),
    enabled: Boolean(tok && ws),
  });
}

export function useCreateProvider() {
  const qc = useQueryClient();
  return useMutation<ProviderRead, unknown, ProviderCreate>({
    mutationFn: (input) => api.post<ProviderRead>("/api/v1/providers", input),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["providers"] }),
  });
}

export function useUpdateProvider(id: string) {
  const qc = useQueryClient();
  return useMutation<ProviderRead, unknown, ProviderUpdate>({
    mutationFn: (input) => api.patch<ProviderRead>(`/api/v1/providers/${id}`, input),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["providers"] }),
  });
}

export function useDeleteProvider() {
  const qc = useQueryClient();
  return useMutation<void, unknown, string>({
    mutationFn: (id) => api.delete(`/api/v1/providers/${id}`),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["providers"] }),
  });
}

export function useReorderProviders() {
  const qc = useQueryClient();
  return useMutation<ProviderRead[], unknown, ProviderReorderRequest>({
    mutationFn: (input) =>
      api.post<ProviderRead[]>("/api/v1/providers/reorder", input),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["providers"] }),
  });
}

// ─── Catalog (static, server-side reflection of pydantic-ai) ────────

export function useProviderCatalog() {
  const tok = useAuthStore((s) => s.accessToken);
  return useQuery<ProviderCatalogEntry[]>({
    queryKey: ["provider-catalog"],
    queryFn: () => api.get<ProviderCatalogEntry[]>("/api/v1/provider-catalog"),
    enabled: Boolean(tok),
    staleTime: 5 * 60 * 1000,
  });
}

// ─── Per-provider models ───────────────────────────────────────────

export function useProviderModels(providerId: string | undefined) {
  const tok = useAuthStore((s) => s.accessToken);
  const ws = useWorkspaceStore((s) => s.activeWorkspaceId);
  return useQuery<ProviderModelRead[]>({
    queryKey: ["provider-models", providerId, ws],
    queryFn: () =>
      api.get<ProviderModelRead[]>(`/api/v1/providers/${providerId}/models`),
    enabled: Boolean(tok && ws && providerId),
  });
}

export function useAddProviderModel(providerId: string) {
  const qc = useQueryClient();
  return useMutation<ProviderModelRead, unknown, ProviderModelManualCreate>({
    mutationFn: (input) =>
      api.post<ProviderModelRead>(
        `/api/v1/providers/${providerId}/models`,
        input,
      ),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["provider-models", providerId] });
    },
  });
}

export function useUpdateProviderModel(providerId: string) {
  const qc = useQueryClient();
  return useMutation<
    ProviderModelRead,
    unknown,
    { modelId: string; patch: ProviderModelUpdate }
  >({
    mutationFn: ({ modelId, patch }) =>
      api.patch<ProviderModelRead>(
        `/api/v1/providers/${providerId}/models/${modelId}`,
        patch,
      ),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["provider-models", providerId] });
    },
  });
}

export function useDeleteProviderModel(providerId: string) {
  const qc = useQueryClient();
  return useMutation<void, unknown, string>({
    mutationFn: (modelId) =>
      api.delete(`/api/v1/providers/${providerId}/models/${modelId}`),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["provider-models", providerId] });
    },
  });
}

export function useReorderProviderModels(providerId: string) {
  const qc = useQueryClient();
  return useMutation<ProviderModelRead[], unknown, ProviderModelReorderRequest>({
    mutationFn: (input) =>
      api.post<ProviderModelRead[]>(
        `/api/v1/providers/${providerId}/models:reorder`,
        input,
      ),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["provider-models", providerId] });
    },
  });
}

// ─── Discover & apply ──────────────────────────────────────────────

export function useDiscoverModels() {
  return useMutation<DiscoverResponse, unknown, string>({
    mutationFn: (providerId) =>
      api.post<DiscoverResponse>(
        `/api/v1/providers/${providerId}/discover`,
        undefined,
      ),
  });
}

export function useApplyDiscoveredModels(providerId: string) {
  const qc = useQueryClient();
  return useMutation<ProviderModelRead[], unknown, DiscoverApplyRequest>({
    mutationFn: (input) =>
      api.post<ProviderModelRead[]>(
        `/api/v1/providers/${providerId}/discover/apply`,
        input,
      ),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["provider-models", providerId] });
    },
  });
}

export function useTestProvider(providerId: string) {
  return useMutation<ProviderTestResponse, unknown, { model?: string }>({
    mutationFn: (input) =>
      api.post<ProviderTestResponse>(
        `/api/v1/providers/${providerId}/test`,
        input,
      ),
  });
}

export function useResolvedModelProfile(
  providerId: string | undefined,
  modelId: string | undefined,
  enabled: boolean,
) {
  return useQuery<ResolvedReasoningProfile>({
    queryKey: ["resolved-model-profile", providerId, modelId],
    queryFn: () =>
      api.get<ResolvedReasoningProfile>(
        `/api/v1/providers/${providerId}/models/${modelId}/profile`,
      ),
    enabled: Boolean(enabled && providerId && modelId),
  });
}
