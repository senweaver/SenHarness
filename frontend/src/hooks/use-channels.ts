"use client";

import { useCallback } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "@/lib/api";
import { useAuthStore } from "@/stores/auth-store";
import { useWorkspaceStore } from "@/stores/workspace-store";

/**
 * IM channel kinds. Must stay in sync with the providers registered
 * in backend/app/services/channels/__init__.py — adding a kind here
 * without a matching backend provider will fail at create time.
 */
export type ChannelKind =
  | "slack"
  | "discord"
  | "teams"
  | "feishu"
  | "lark"
  | "wecom"
  | "wechat"
  | "dingtalk"
  | "telegram"
  | "qq"
  | "webhook";

/**
 * Shape returned by GET /api/v1/channels/kinds — drives the
 * provider picker + schema-driven config form. The backend builds
 * this from each provider's ChannelProviderMeta.
 */
export type ChannelMode = "webhook" | "stream";

export interface ChannelKindMeta {
  kind: ChannelKind;
  display_name: string;
  description: string;
  docs_url: string;
  required_config_fields: string[];
  optional_config_fields: string[];
  supports_outbound: boolean;
  /** "webhook" | "stream" — modes the provider can run in. */
  supported_modes?: ChannelMode[];
  /** Default mode when the operator hasn't picked one. */
  default_mode?: ChannelMode;
  /** Optional pip extra needed for stream mode (null if no extra). */
  stream_requires_extra?: string | null;
  /** True iff this deployment can actually run stream mode now. */
  stream_available?: boolean;
  /**
   * Per-mode field overrides. When the active mode appears here, the
   * channel-create form should render only those fields instead of the
   * global ``required_config_fields`` / ``optional_config_fields``.
   * ``null`` means the provider hasn't bothered with mode-specific
   * splits and the form should fall back to the globals.
   */
  mode_required_fields?: Partial<Record<ChannelMode, string[]>> | null;
  mode_optional_fields?: Partial<Record<ChannelMode, string[]>> | null;
  mode_hidden_fields?: Partial<Record<ChannelMode, string[]>> | null;
}

/** Stream / connection introspection — drives the channel-card status badge. */
export interface ChannelStatus {
  channel_id: string;
  kind: ChannelKind;
  mode: "webhook" | "stream";
  enabled: boolean;
  connected: boolean;
  last_event_at: string | null;
  last_error: string | null;
  started_at: string | null;
  reconnect_attempts: number;
}

export interface WeChatQrSession {
  qr_id: string;
  /** HTTPS image URL from iLink — use as ``<img src>`` (not base64). */
  qrcode_image_data: string;
  expires_in: number;
  status: "pending" | "scanned" | "confirmed" | "expired" | "error";
  bot_token?: string;
  error?: string;
}

interface ChannelKindsResponse {
  providers: ChannelKindMeta[];
  count: number;
}

export type SenderAllowlistMode = "allow_all" | "allow_listed" | "deny_listed";

export interface SenderAllowlistRules {
  mode?: SenderAllowlistMode;
  allow?: string[];
  deny?: string[];
}

/** Where a channel resolves its agent pool from (P0 + P2 multi-agent routing). */
export type BindScope = "agent" | "workspace" | "user" | "squad";
/** Per-surface access policy for inbound senders. */
export type ChannelPolicy = "open" | "allowlist" | "disabled" | "pairing";
/** How the agent picker is rendered back to the chat user. */
export type MenuStyle = "auto" | "text" | "buttons";
/** How replies are attributed to the answering agent. */
export type ReplyAttribution = "prefix" | "identity" | "off";
/** Group stickiness sharing model (P1). */
export type GroupOverride = "shared" | "per_sender";
/** Natural-language handoff behaviour (P1). */
export type HandoffMode = "switch" | "suggest";

/**
 * One deterministic natural-language handoff rule (P1). When an inbound
 * message contains any keyword the route either switches to ``target``
 * outright (``switch``) or the main agent proactively proposes the
 * handoff (``suggest``). Mirrors ``ChannelHandoffRule`` on the backend.
 */
export interface ChannelHandoffRule {
  keywords: string[];
  /** Target agent: alias, ``#index`` against the menu, or an agent id. */
  target: string;
  mode: HandoffMode;
}

/**
 * Wire shape of ``channels.routing_config_json``. Mirrors
 * ``ChannelRoutingConfig`` in backend/app/schemas/channel.py. An absent
 * blob (or ``bind_scope=agent``) is the legacy one-channel↔one-agent
 * behaviour anchored on ``default_agent_id``.
 */
export interface ChannelRoutingConfig {
  bind_scope: BindScope;
  /** workspace_id when bind_scope=workspace (null → the channel's own workspace). */
  scope_ref_id?: string | null;
  /** Narrows the resolved pool to these agent ids when set. */
  allowlist_agent_ids?: string[] | null;
  dm_policy: ChannelPolicy;
  group_policy: ChannelPolicy;
  menu_style: MenuStyle;
  selection_window_seconds: number;
  reply_attribution: ReplyAttribution;
  /** P1 — ``shared`` (one route per group) or ``per_sender`` override. */
  group_override: GroupOverride;
  /** P1 — deterministic keyword handoff rules. */
  handoff_rules: ChannelHandoffRule[];
}

/**
 * Match scope of a layered binding rule (P1 "most-specific-wins").
 * ``peer`` / ``group`` / ``channel_default`` are matched today; the rest
 * are reserved rungs of the specificity ladder.
 */
export type BindingMatchScope =
  | "peer"
  | "thread"
  | "role"
  | "guild"
  | "team"
  | "account"
  | "group"
  | "channel_default";

/** A single layered routing binding row (P1). */
export interface ChannelBinding {
  id: string;
  channel_id: string;
  match_scope: BindingMatchScope;
  match_value: string | null;
  bind_scope: BindScope | null;
  scope_ref_id: string | null;
  target_agent_id: string | null;
  allowlist_agent_ids: string[] | null;
  priority: number;
  created_at: string;
  updated_at: string;
}

export interface ChannelBindingCreateInput {
  match_scope: BindingMatchScope;
  match_value?: string | null;
  bind_scope?: BindScope | null;
  scope_ref_id?: string | null;
  target_agent_id?: string | null;
  allowlist_agent_ids?: string[] | null;
  priority?: number;
}

/** Response of ``POST /channels/{id}/bind-codes``. */
export interface ChannelBindCode {
  code: string;
  ttl_seconds: number;
}

export interface ChannelRead {
  id: string;
  workspace_id: string;
  name: string;
  kind: ChannelKind;
  inbound_token: string;
  config_json: Record<string, unknown>;
  default_agent_id: string | null;
  default_squad_id: string | null;
  enabled: boolean;
  metadata_json: Record<string, unknown>;
  sender_allowlist_json: SenderAllowlistRules;
  routing_config_json: Partial<ChannelRoutingConfig>;
  created_at: string;
  updated_at: string;
  created_by: string | null;
}

export interface ChannelCreateInput {
  name: string;
  kind: ChannelKind;
  config_json?: Record<string, unknown>;
  default_agent_id?: string | null;
  default_squad_id?: string | null;
  enabled?: boolean;
  metadata_json?: Record<string, unknown>;
  sender_allowlist_json?: SenderAllowlistRules;
  routing_config_json?: ChannelRoutingConfig | null;
}

export type ChannelUpdateInput = Partial<ChannelCreateInput>;

export function useChannels() {
  const token = useAuthStore((s) => s.accessToken);
  const ws = useWorkspaceStore((s) => s.activeWorkspaceId);
  return useQuery<ChannelRead[]>({
    queryKey: ["channels", ws],
    queryFn: () => api.get<ChannelRead[]>("/api/v1/channels"),
    enabled: Boolean(token && ws),
  });
}

/**
 * Cache the kinds list aggressively — it only changes when the
 * deployment ships a new provider plugin.
 */
export function useChannelKinds() {
  const token = useAuthStore((s) => s.accessToken);
  return useQuery<ChannelKindMeta[]>({
    queryKey: ["channel-kinds"],
    queryFn: async () => {
      const res = await api.get<ChannelKindsResponse>("/api/v1/channels/kinds");
      return res.providers;
    },
    enabled: Boolean(token),
    staleTime: 5 * 60 * 1000,
  });
}

export function useChannel(id: string | null | undefined) {
  return useQuery<ChannelRead>({
    queryKey: ["channel", id],
    queryFn: () => api.get<ChannelRead>(`/api/v1/channels/${id}`),
    enabled: Boolean(id),
  });
}

export function useCreateChannel() {
  const qc = useQueryClient();
  return useMutation<ChannelRead, unknown, ChannelCreateInput>({
    mutationFn: (input) => api.post<ChannelRead>("/api/v1/channels", input),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["channels"] }),
  });
}

export function useUpdateChannel(id: string) {
  const qc = useQueryClient();
  return useMutation<ChannelRead, unknown, ChannelUpdateInput>({
    mutationFn: (input) =>
      api.patch<ChannelRead>(`/api/v1/channels/${id}`, input),
    onSuccess: (updated) => {
      qc.setQueryData(["channel", id], updated);
      qc.invalidateQueries({ queryKey: ["channels"] });
    },
  });
}

export function useDeleteChannel() {
  const qc = useQueryClient();
  return useMutation<void, unknown, string>({
    mutationFn: (id) => api.delete(`/api/v1/channels/${id}`),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["channels"] }),
  });
}

export function useRotateChannelToken(id: string) {
  const qc = useQueryClient();
  return useMutation<ChannelRead, unknown, void>({
    mutationFn: () =>
      api.post<ChannelRead>(`/api/v1/channels/${id}/rotate-token`, {}),
    onSuccess: (updated) => {
      qc.setQueryData(["channel", id], updated);
      qc.invalidateQueries({ queryKey: ["channels"] });
    },
  });
}

/**
 * Mint a one-time ``/bind`` code for the current identity on a channel.
 * The operator hands the code to a chat user who replies ``/bind <code>``
 * to link their platform account. Not cached — every press mints fresh.
 */
export function useCreateBindCode(id: string) {
  return useMutation<ChannelBindCode, unknown, void>({
    mutationFn: () =>
      api.post<ChannelBindCode>(`/api/v1/channels/${id}/bind-codes`, {}),
  });
}

/**
 * Poll the streaming runtime status for a channel. Lightweight (5s
 * interval) so the UI can paint the connection LED without bloating
 * the API tier with constant traffic.
 */
export function useChannelStatus(id: string | null | undefined) {
  return useQuery<ChannelStatus>({
    queryKey: ["channel-status", id],
    queryFn: () => api.get<ChannelStatus>(`/api/v1/channels/${id}/status`),
    enabled: Boolean(id),
    refetchInterval: 5000,
    staleTime: 0,
  });
}

/**
 * WeChat iLink QR-login flow: ``startQr`` kicks off a fresh QR, the
 * returned ``qr.qr_id`` plus ``poll`` drives the dialog. Status
 * ``confirmed`` is the success terminal — backend has already
 * persisted the bot_token by the time we see it.
 */
export function useWeChatQrLogin(id: string) {
  const qc = useQueryClient();
  const start = useMutation<WeChatQrSession, unknown, void>({
    mutationFn: () =>
      api.post<WeChatQrSession>(`/api/v1/channels/${id}/wechat/qr`, {}),
  });
  // ``poll`` MUST be stable across renders — the dialog uses it inside
  // a ``setTimeout`` chain whose useEffect depends on the function
  // identity; if we rebuild it each render the effect tears the timer
  // down and re-arms it on every parent re-render, producing a polling
  // storm. Memoise on ``id`` so the only thing that changes the
  // identity is a different channel.
  const poll = useCallback(
    (qrId: string) =>
      api.get<WeChatQrSession>(`/api/v1/channels/${id}/wechat/qr/${qrId}`),
    [id],
  );
  const logout = useMutation<void, unknown, void>({
    mutationFn: () => api.delete(`/api/v1/channels/${id}/wechat/session`),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["channels"] });
      qc.invalidateQueries({ queryKey: ["channel-status", id] });
    },
  });
  return { start, poll, logout };
}

/**
 * Layered binding rules for a channel (P1 "most-specific-wins"). Resolved
 * at dispatch time by ``channel_routing.resolve_binding`` — an empty list
 * degrades to the channel-level default (the P0 behaviour).
 */
export function useChannelBindings(id: string | null | undefined) {
  return useQuery<ChannelBinding[]>({
    queryKey: ["channel-bindings", id],
    queryFn: () => api.get<ChannelBinding[]>(`/api/v1/channels/${id}/bindings`),
    enabled: Boolean(id),
  });
}

export function useCreateChannelBinding(id: string) {
  const qc = useQueryClient();
  return useMutation<ChannelBinding, unknown, ChannelBindingCreateInput>({
    mutationFn: (input) =>
      api.post<ChannelBinding>(`/api/v1/channels/${id}/bindings`, input),
    onSuccess: () =>
      qc.invalidateQueries({ queryKey: ["channel-bindings", id] }),
  });
}

export function useDeleteChannelBinding(id: string) {
  const qc = useQueryClient();
  return useMutation<void, unknown, string>({
    mutationFn: (bindingId) =>
      api.delete(`/api/v1/channels/${id}/bindings/${bindingId}`),
    onSuccess: () =>
      qc.invalidateQueries({ queryKey: ["channel-bindings", id] }),
  });
}
