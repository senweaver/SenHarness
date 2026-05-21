/**
 * Session WebSocket client that speaks the SenHarness streaming event protocol.
 *
 * Server → client frames:
 *   delta · thinking · tool_call · tool_result · approval_request ·
 *   approval_update · usage · error · final · pong · resume_ack ·
 *   session_title_updated
 *
 * Client → server frames:
 *   user_message · approval_decision · ping · cancel · resume
 *
 * **Import contract**
 *
 *   - Runtime exports (``openSessionWs`` / ``send*`` / ``WS_CLOSE``) are
 *     allowed only in the WebSocket transport adapter
 *     ([`lib/chat-transport.ts`](./chat-transport.ts)) and the
 *     control-plane hook
 *     ([`hooks/use-session-control.ts`](../hooks/use-session-control.ts)).
 *     Business components must consume the WS through one of those two
 *     surfaces — never call ``new WebSocket`` or ``sendApprovalDecision``
 *     from a React component or page handler.
 *   - Type-only exports (``SessionEvent`` / ``ChatMode`` /
 *     ``ServerFrameEnvelope`` / ``SendUserMessageOptions``) are TypeScript-
 *     erased and free for any consumer; we re-publish them so stores +
 *     composers can stay strongly typed without paying a runtime import.
 */

import { useAuthStore } from "@/stores/auth-store";

const CONFIGURED_WS_BASE =
  process.env.NEXT_PUBLIC_WS_BASE_URL?.replace(/\/$/, "") ?? "ws://localhost:8000";

// NEXT_PUBLIC_WS_BASE_URL is baked in at build time (typically
// ws://localhost:8000). When opened over a LAN IP or alternate
// hostname, a loopback host points the browser at the visitor's own
// machine. Rewrite the host to the current page hostname on the client,
// keeping the configured protocol and port.
function resolveWsBase(): string {
  if (typeof window === "undefined") return CONFIGURED_WS_BASE;
  try {
    const parsed = new URL(CONFIGURED_WS_BASE);
    const isLoopback = parsed.hostname === "localhost" || parsed.hostname === "127.0.0.1";
    const browserHost = window.location.hostname;
    if (isLoopback && browserHost && browserHost !== "localhost" && browserHost !== "127.0.0.1") {
      parsed.hostname = browserHost;
      return parsed.toString().replace(/\/$/, "");
    }
  } catch {
    return CONFIGURED_WS_BASE;
  }
  return CONFIGURED_WS_BASE;
}

const WS_BASE = resolveWsBase();

/**
 * Every server→client frame (other than the bare ``pong``) carries a
 * monotonic ``seq`` field starting at 1. The frontend tracks the highest
 * ``seq`` it has observed and replays it via the ``resume`` frame on
 * reconnect so the backend can re-emit anything the client missed during
 * a transient disconnect.
 *
 * ``run_id`` is also stamped on every frame produced during a turn so a
 * reconnect can scope the replay to a specific run.
 */
export interface ServerFrameEnvelope {
  seq?: number;
  run_id?: string;
}

export type SessionEvent =
  | { type: "delta"; data: ServerFrameEnvelope & { text: string } }
  | { type: "thinking"; data: ServerFrameEnvelope & { text: string } }
  | {
      type: "tool_call";
      data: ServerFrameEnvelope & {
        id: string;
        name: string;
        args: Record<string, unknown>;
      };
    }
  | {
      type: "tool_result";
      data: ServerFrameEnvelope & {
        id: string;
        result: unknown;
        truncated?: boolean;
      };
    }
  | {
      type: "approval_request";
      data: ServerFrameEnvelope & {
        id: string;
        tool_name: string;
        tool_args: Record<string, unknown>;
        summary: string | null;
        created_at: string;
        expires_at: string;
        session_id: string;
      };
    }
  | {
      type: "approval_update";
      data: ServerFrameEnvelope & {
        id: string;
        status: string;
        reason?: string | null;
      };
    }
  | {
      type: "usage";
      data: ServerFrameEnvelope & {
        tokens: Record<string, number>;
        cost: number;
      };
    }
  | {
      type: "error";
      data: ServerFrameEnvelope & {
        code: string;
        message: string;
        retryable: boolean;
      };
    }
  | {
      type: "final";
      data: ServerFrameEnvelope & {
        message_id: string;
        summary?: string | null;
        reason?: string | null;
      };
    }
  | {
      type: "session_title_updated";
      data: ServerFrameEnvelope & { session_id: string; title: string };
    }
  | {
      type: "system";
      data: ServerFrameEnvelope & {
        kind: string;
        [key: string]: unknown;
      };
    }
  | {
      type: "resume_ack";
      data: {
        last_seen_seq: number | null;
        replayed: number;
        current_seq: number;
      };
    }
  | { type: "pong" };

export interface SessionWsHandlers {
  onEvent?: (event: SessionEvent) => void;
  onOpen?: () => void;
  onClose?: (code: number) => void;
  onError?: (err: Event) => void;
}

export function openSessionWs(sessionId: string, handlers: SessionWsHandlers = {}): WebSocket {
  // Always read the freshest token at connection time so a previously
  // silently-refreshed access token is used instead of the stale one the
  // page captured on mount.
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

/** WebSocket close codes the backend can send. */
export const WS_CLOSE = {
  AUTH_EXPIRED: 4401,
  FORBIDDEN: 4403,
  NOT_FOUND: 4404,
} as const;

export type ChatMode = "flash" | "thinking" | "plan" | "subagent";

export interface SendUserMessageOptions {
  attachmentIds?: string[];
  /** Composer mode hint — backend folds it into the run's policy block. */
  mode?: ChatMode | null;
  /**
   * Optional ``provider:model`` override for this single turn. When unset,
   * the backend falls back to the user's saved preference for this agent
   * (``Identity.profile_json.chat_model_prefs``).
   */
  model?: string | null;
}

export function sendUserMessage(
  ws: WebSocket,
  text: string,
  optsOrAttachmentIds?: SendUserMessageOptions | string[],
): void {
  const opts: SendUserMessageOptions = Array.isArray(optsOrAttachmentIds)
    ? { attachmentIds: optsOrAttachmentIds }
    : (optsOrAttachmentIds ?? {});
  const data: Record<string, unknown> = { text };
  if (opts.attachmentIds && opts.attachmentIds.length > 0) {
    data.attachment_ids = opts.attachmentIds;
  }
  if (opts.mode) {
    data.mode = opts.mode;
  }
  if (opts.model) {
    data.model = opts.model;
  }
  ws.send(JSON.stringify({ type: "user_message", data }));
}

export function sendPing(ws: WebSocket): void {
  ws.send(JSON.stringify({ type: "ping" }));
}

/**
 * Ask the server to replay every frame whose ``seq > last_seen_seq``.
 * Sent immediately after a reconnect on the previously-active session WS.
 *
 * ``runId`` narrows the replay to a specific run — handy when only the
 * current turn matters and earlier ones have already been persisted to the
 * DB. Omit it to replay everything still in the connection's cache.
 */
export function sendResume(
  ws: WebSocket,
  lastSeenSeq: number,
  runId?: string,
): void {
  const data: Record<string, unknown> = { last_seen_seq: lastSeenSeq };
  if (runId) data.run_id = runId;
  ws.send(JSON.stringify({ type: "resume", data }));
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
