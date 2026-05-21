"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "@/lib/api";
import { useAuthStore } from "@/stores/auth-store";
import { useWorkspaceStore } from "@/stores/workspace-store";
import type {
  NotificationPrefsRead,
  NotificationPrefsUpdate,
  NotificationRead,
  UnreadNotificationCount,
} from "@/types/api";

export interface NotificationListFilters {
  unreadOnly?: boolean;
  readOnly?: boolean;
  limit?: number;
  offset?: number;
  eventKey?: string | null;
  urgency?: string | null;
  q?: string;
  enabled?: boolean;
  refetchIntervalMs?: number;
}

/**
 * Bell list — workspace-scoped server-state. Default refresh cadence is
 * 60s for the popover (the websocket already pushes new rows live); the
 * inbox page passes ``refetchIntervalMs: 30_000`` so manual operators
 * see updates without hitting Refresh.
 */
export function useNotifications(opts: NotificationListFilters = {}) {
  const tok = useAuthStore((s) => s.accessToken);
  const ws = useWorkspaceStore((s) => s.activeWorkspaceId);
  const params = new URLSearchParams();
  if (opts.unreadOnly) params.set("unread_only", "true");
  if (opts.readOnly) params.set("read_only", "true");
  if (opts.limit) params.set("limit", String(opts.limit));
  if (opts.offset) params.set("offset", String(opts.offset));
  if (opts.eventKey) params.set("event_key", opts.eventKey);
  if (opts.urgency) params.set("urgency", opts.urgency);
  const trimmedQ = opts.q?.trim();
  if (trimmedQ) params.set("q", trimmedQ);
  return useQuery<NotificationRead[]>({
    queryKey: [
      "notifications",
      ws,
      opts.unreadOnly ?? false,
      opts.readOnly ?? false,
      opts.limit ?? 50,
      opts.offset ?? 0,
      opts.eventKey ?? null,
      opts.urgency ?? null,
      trimmedQ ?? null,
    ],
    queryFn: () =>
      api.get<NotificationRead[]>(
        `/api/v1/notifications?${params.toString()}`,
      ),
    enabled: Boolean(tok && ws) && (opts.enabled ?? true),
    refetchInterval: opts.refetchIntervalMs ?? 60_000,
  });
}

/**
 * Single-row fetch for the inbox detail drawer. Cache-keyed by id only,
 * but invalidated together with the list whenever a mark mutation runs.
 */
export function useNotification(id: string | null) {
  const tok = useAuthStore((s) => s.accessToken);
  const ws = useWorkspaceStore((s) => s.activeWorkspaceId);
  return useQuery<NotificationRead>({
    queryKey: ["notifications", "detail", ws, id],
    queryFn: () =>
      api.get<NotificationRead>(`/api/v1/notifications/${id}`),
    enabled: Boolean(tok && ws && id),
  });
}

export function useUnreadNotificationCount(opts?: { enabled?: boolean }) {
  const tok = useAuthStore((s) => s.accessToken);
  const ws = useWorkspaceStore((s) => s.activeWorkspaceId);
  return useQuery<UnreadNotificationCount>({
    queryKey: ["notifications", "unread-count", ws],
    queryFn: () =>
      api.get<UnreadNotificationCount>("/api/v1/notifications/unread-count"),
    enabled: Boolean(tok && ws) && (opts?.enabled ?? true),
    refetchInterval: 30_000,
  });
}

export function useMarkNotificationRead() {
  const qc = useQueryClient();
  return useMutation<NotificationRead, unknown, { id: string }>({
    mutationFn: ({ id }) =>
      api.post<NotificationRead>(`/api/v1/notifications/${id}/read`, {}),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["notifications"] });
    },
  });
}

export function useMarkNotificationUnread() {
  const qc = useQueryClient();
  return useMutation<NotificationRead, unknown, { id: string }>({
    mutationFn: ({ id }) =>
      api.post<NotificationRead>(`/api/v1/notifications/${id}/unread`, {}),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["notifications"] });
    },
  });
}

export function useMarkAllNotificationsRead() {
  const qc = useQueryClient();
  return useMutation<{ marked: number }>({
    mutationFn: () =>
      api.post<{ marked: number }>(
        "/api/v1/notifications/mark-all-read",
        {},
      ),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["notifications"] });
    },
  });
}

export function useNotificationPrefs() {
  const tok = useAuthStore((s) => s.accessToken);
  return useQuery<NotificationPrefsRead>({
    queryKey: ["notifications", "prefs"],
    queryFn: () =>
      api.get<NotificationPrefsRead>("/api/v1/me/notification-prefs"),
    enabled: Boolean(tok),
  });
}

export function useUpdateNotificationPrefs() {
  const qc = useQueryClient();
  return useMutation<
    NotificationPrefsRead,
    unknown,
    NotificationPrefsUpdate
  >({
    mutationFn: (body) =>
      api.put<NotificationPrefsRead>(
        "/api/v1/me/notification-prefs",
        body,
      ),
    onSuccess: (data) => {
      qc.setQueryData(["notifications", "prefs"], data);
    },
  });
}
