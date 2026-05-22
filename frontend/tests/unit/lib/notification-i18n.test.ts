import { describe, expect, it, vi } from "vitest";

import {
  resolveNotificationBody,
  resolveNotificationTitle,
  type NamespaceTranslator,
} from "@/lib/notification-i18n";
import type { NotificationRead } from "@/types/api";

function makeTranslator(
  catalog: Record<string, string>,
): NamespaceTranslator {
  const fn = vi.fn((key: string, values?: Record<string, string | number>) => {
    const template = catalog[key];
    if (!template) throw new Error(`missing:${key}`);
    return template.replace(/\{([a-zA-Z_][a-zA-Z0-9_]*)\}/g, (_, name: string) =>
      String(values?.[name] ?? `{${name}}`),
    );
  }) as unknown as NamespaceTranslator;
  return fn;
}

function makeRow(
  overrides: Partial<NotificationRead> = {},
): NotificationRead {
  return {
    id: "11111111-1111-1111-1111-111111111111",
    workspace_id: "22222222-2222-2222-2222-222222222222",
    recipient_identity_id: "33333333-3333-3333-3333-333333333333",
    actor_identity_id: null,
    kind: "goal.unlocked",
    level: "info",
    title: "notification.goalUnlocked.title",
    body: "notification.goalUnlocked.message",
    resource_type: null,
    resource_id: null,
    action_url: null,
    metadata_json: {
      title_key: "notification.goalUnlocked.title",
      message_key: "notification.goalUnlocked.message",
      payload: { goal_text: "Ship M0" },
    },
    read_at: null,
    created_at: "2026-01-01T00:00:00Z",
    updated_at: "2026-01-01T00:00:00Z",
    ...overrides,
  };
}

describe("resolveNotificationTitle", () => {
  it("localizes via metadata keys and payload", () => {
    const tNs = makeTranslator({
      "notification.goalUnlocked.title": "Goal unlocked",
      "notification.goalUnlocked.message": "Unlocked: {goal_text}",
    });
    const row = makeRow();
    expect(resolveNotificationTitle(row, tNs)).toBe("Goal unlocked");
    expect(resolveNotificationBody(row, tNs)).toBe("Unlocked: Ship M0");
  });

  it("falls back to stored title when translation is missing", () => {
    const tNs = makeTranslator({});
    const row = makeRow({ title: "Plain title" });
    expect(resolveNotificationTitle(row, tNs)).toBe("Plain title");
  });

  it("uses stored value as i18n key when metadata omits keys", () => {
    const tNs = makeTranslator({
      "notification.goalLocked.title": "Goal locked",
    });
    const row = makeRow({
      title: "notification.goalLocked.title",
      metadata_json: {},
    });
    expect(resolveNotificationTitle(row, tNs)).toBe("Goal locked");
  });
});
