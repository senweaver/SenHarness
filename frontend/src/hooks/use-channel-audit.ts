"use client";

import { useQuery } from "@tanstack/react-query";

import { api } from "@/lib/api";

export interface ChannelAuditEvent {
  id: string;
  action: string;
  summary: string | null;
  metadata_json: Record<string, unknown>;
  created_at: string;
  actor_identity_id: string | null;
  resource_id: string | null;
}

const RATE_LIMIT_ACTIONS = "channel.rate_limited";

/**
 * Recent rate-limit hits for one channel — drives the
 * "Recent rate limit hits" card on the channel detail surface so
 * admins can see whether per-sender or per-channel buckets are
 * tripping today.
 */
export function useChannelRateLimitHits(channelId: string | null | undefined) {
  return useQuery<ChannelAuditEvent[]>({
    queryKey: ["channel-audit", "rate_limited", channelId],
    queryFn: async () => {
      const rows = await api.get<ChannelAuditEvent[]>(
        `/api/v1/audit/events?action=${encodeURIComponent(RATE_LIMIT_ACTIONS)}&resource_id=${channelId}&limit=20`,
      );
      return rows;
    },
    enabled: Boolean(channelId),
    refetchInterval: 30_000,
    staleTime: 15_000,
  });
}
