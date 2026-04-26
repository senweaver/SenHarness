/**
 * Session WebSocket client that speaks the SenHarness streaming event protocol:
 *
 *   { type: "delta" | "thinking" | "tool_call" | "tool_result" |
 *           "approval_request" | "approval_update" |
 *           "usage" | "error" | "final" | "pong", data: {...} }
 */

import { useAuthStore } from "@/stores/auth-store";

const WS_BASE =
  process.env.NEXT_PUBLIC_WS_BASE_URL?.replace(/\/$/, "") ?? "ws://localhost:8000";

export type SessionEvent =
  | { type: "delta"; data: { text: string } }
  | { type: "thinking"; data: { text: string } }
  | { type: "tool_call"; data: { id: string; name: string; args: Record<string, unknown> } }
  | { type: "tool_result"; data: { id: string; result: unknown; truncated?: boolean } }
  | {
      type: "approval_request";
      data: {
        id: string;
        tool_name: string;
        tool_args: Record<string, unknown>;
        summary: string | null;
        created_at: string;
        expires_at: string;
        session_id: string;
      };
    }
  | { type: "approval_update"; data: { id: string; status: string; reason?: string | null } }
  | { type: "usage"; data: { tokens: Record<string, number>; cost: number } }
  | { type: "error"; data: { code: string; message: string; retryable: boolean } }
  | { type: "final"; data: { message_id: string; summary?: string | null } }
  | { type: "pong" };

export interface SessionWsHandlers {
  onEvent?: (event: SessionEvent) => void;
  onOpen?: () => void;
  onClose?: (code: number) => void;
  onError?: (err: Event) => void;
}

export function openSessionWs(sessionId: string, handlers: SessionWsHandlers = {}): WebSocket {
  const token = useAuthStore.getState().accessToken ?? "";
  const url = `${WS_BASE}/api/v1/sessions/ws/${sessionId}?token=${encodeURIComponent(token)}`;
  const ws = new WebSocket(url);

  ws.addEventListener("open", () => handlers.onOpen?.());
  ws.addEventListener("close", (e) => handlers.onClose?.(e.code));
  ws.addEventListener("error", (e) => handlers.onError?.(e));
  ws.addEventListener("message", (msg) => {
    try {
      const event = JSON.parse(msg.data) as SessionEvent;
      handlers.onEvent?.(event);
    } catch {
      handlers.onError?.(new Event("ws.parse_error"));
    }
  });
  return ws;
}

export function sendUserMessage(
  ws: WebSocket,
  text: string,
  attachmentIds?: string[],
): void {
  const data: Record<string, unknown> = { text };
  if (attachmentIds && attachmentIds.length > 0) {
    data.attachment_ids = attachmentIds;
  }
  ws.send(JSON.stringify({ type: "user_message", data }));
}

export function sendPing(ws: WebSocket): void {
  ws.send(JSON.stringify({ type: "ping" }));
}

export function sendApprovalDecision(
  ws: WebSocket,
  approvalId: string,
  action: "approve" | "deny",
  reason?: string,
): void {
  ws.send(
    JSON.stringify({
      type: "approval_decision",
      data: { approval_id: approvalId, action, reason: reason ?? "" },
    }),
  );
}

/**
 * Cancel the in-flight turn (and optionally a specific run's pending approvals).
 * Server interprets a missing ``run_id`` as "cancel everything in this session".
 */
export function sendCancel(ws: WebSocket, runId?: string): void {
  ws.send(
    JSON.stringify({
      type: "cancel",
      data: runId ? { run_id: runId } : {},
    }),
  );
}
