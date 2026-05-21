/**
 * Resolver tests. The mapping from `event_key` → in-app destination
 * is the public contract of the notification inbox: a wrong link is
 * worse than no link because it sends users to an unrelated page.
 *
 * Each branch covers (a) the happy payload-driven case, (b) the
 * fallback when the deep-link payload is missing, and (c) one of the
 * `action_url` overrides.
 */
import { describe, expect, it } from "vitest";

import {
  flattenNotificationPayload,
  resolveNotificationSource,
} from "@/lib/notification-source";
import type { NotificationRead } from "@/types/api";

function makeRow(
  overrides: Partial<NotificationRead> & { kind: string },
): NotificationRead {
  return {
    id: "11111111-1111-1111-1111-111111111111",
    workspace_id: "22222222-2222-2222-2222-222222222222",
    recipient_identity_id: "33333333-3333-3333-3333-333333333333",
    actor_identity_id: null,
    kind: overrides.kind,
    level: overrides.level ?? "info",
    title: overrides.title ?? "Notification",
    body: overrides.body ?? null,
    resource_type: overrides.resource_type ?? null,
    resource_id: overrides.resource_id ?? null,
    action_url: overrides.action_url ?? null,
    metadata_json: overrides.metadata_json ?? {},
    read_at: overrides.read_at ?? null,
    created_at: overrides.created_at ?? new Date().toISOString(),
    updated_at: overrides.updated_at ?? new Date().toISOString(),
  };
}

describe("resolveNotificationSource", () => {
  it("prefers action_url when the dispatcher set one", () => {
    const row = makeRow({
      kind: "goal.alignment_low",
      action_url: "/chat/abc?focus=msg-7",
      metadata_json: { payload: { session_id: "sess-1" } },
    });
    expect(resolveNotificationSource(row)?.href).toBe("/chat/abc?focus=msg-7");
  });

  it("deep-links goal.alignment_low to the chat session", () => {
    const row = makeRow({
      kind: "goal.alignment_low",
      metadata_json: { payload: { session_id: "sess-42" } },
    });
    const target = resolveNotificationSource(row);
    expect(target?.href).toBe("/chat/sess-42");
    expect(target?.kind).toBe("alignment");
    expect(target?.payloadVars).toEqual({ session: "sess-42" });
  });

  it("returns null when goal.alignment_low has no session_id payload", () => {
    const row = makeRow({ kind: "goal.alignment_low" });
    expect(resolveNotificationSource(row)).toBeNull();
  });

  it("routes judge.score_negative to /traces/{session_id}", () => {
    const row = makeRow({
      kind: "judge.score_negative",
      metadata_json: { payload: { session_id: "sess-9", run_id: "run-x" } },
    });
    const target = resolveNotificationSource(row);
    expect(target?.href).toBe("/traces/sess-9");
    expect(target?.payloadVars.run).toBe("run-x");
  });

  it("routes channel.sender_blocked to channels page", () => {
    const row = makeRow({
      kind: "channel.sender_blocked",
      metadata_json: { payload: { channel_id: "ch-1" } },
    });
    expect(resolveNotificationSource(row)?.href).toBe(
      "/channels?channel=ch-1",
    );
  });

  it("routes channel.sender_blocked to the index when no channel id", () => {
    const row = makeRow({ kind: "channel.sender_blocked" });
    expect(resolveNotificationSource(row)?.href).toBe("/channels");
  });

  it("routes security.signature_failed to the audit log", () => {
    const row = makeRow({ kind: "security.signature_failed" });
    expect(resolveNotificationSource(row)?.href).toBe("/settings/audit");
  });

  it("routes workspace.quota_exceeded to the quota page", () => {
    const row = makeRow({ kind: "workspace.quota_exceeded" });
    expect(resolveNotificationSource(row)?.href).toBe(
      "/settings/workspace/quota",
    );
  });

  it("routes inflight_run.lost_detected to the chat session when present", () => {
    const row = makeRow({
      kind: "inflight_run.lost_detected",
      metadata_json: { payload: { session_id: "sess-77" } },
    });
    expect(resolveNotificationSource(row)?.href).toBe("/chat/sess-77");
  });

  it("falls back to runtime console for inflight_run.lost without session_id", () => {
    const row = makeRow({ kind: "inflight_run.lost_detected" });
    expect(resolveNotificationSource(row)?.href).toBe(
      "/settings/system/runtime",
    );
  });

  it("returns null for unknown event keys", () => {
    const row = makeRow({ kind: "totally.unknown_kind" });
    expect(resolveNotificationSource(row)).toBeNull();
  });
});

describe("flattenNotificationPayload", () => {
  it("returns an empty list when the row has no payload", () => {
    expect(flattenNotificationPayload(makeRow({ kind: "x" }))).toEqual([]);
  });

  it("stringifies primitive values inline", () => {
    const row = makeRow({
      kind: "x",
      metadata_json: { payload: { user_name: "Ada", retries: 3, ok: true } },
    });
    expect(flattenNotificationPayload(row)).toEqual([
      ["user_name", "Ada"],
      ["retries", "3"],
      ["ok", "true"],
    ]);
  });

  it("JSON-encodes nested objects rather than dropping them", () => {
    const row = makeRow({
      kind: "x",
      metadata_json: { payload: { nested: { a: 1, b: [2, 3] } } },
    });
    expect(flattenNotificationPayload(row)).toEqual([
      ["nested", '{"a":1,"b":[2,3]}'],
    ]);
  });

  it("skips null and undefined entries", () => {
    const row = makeRow({
      kind: "x",
      metadata_json: { payload: { keep: "yes", drop: null } },
    });
    expect(flattenNotificationPayload(row)).toEqual([["keep", "yes"]]);
  });
});
