"use client";

/**
 * SenHarness WebSocket → Vercel AI SDK `ChatTransport` adapter.
 *
 * Adapted from the Vercel AI SDK ``ChatTransport`` interface:
 * see https://ai-sdk.dev/docs/reference/types/chat-transport
 *
 * Why a custom transport: the SenHarness backend speaks a single bidirectional
 * WebSocket protocol (``/api/v1/sessions/ws/:id``) that already covers HITL
 * approvals, multi-tab broadcast, cancel, etc. We don't want a parallel SSE
 * data plane just to satisfy the SDK's default HTTP transport. This adapter
 * maps the existing ``RunEvent`` frames onto the standard
 * ``ReadableStream<UIMessageChunk>`` shape that ``useChat`` expects.
 *
 * Wire mapping (SenHarness frame → UIMessageChunk):
 *
 *     delta.text                → text-delta
 *     thinking.text             → reasoning-delta
 *     tool_call (id/name/args)  → tool-input-start + tool-input-available
 *     tool_result (id/result)   → tool-output-available
 *     usage                     → data-usage (custom data part)
 *     final.message_id          → finish (+ message-metadata for ids)
 *     error                     → error (errorText carries the ``code``)
 *
 * Frames that are NOT part of the chat surface (``approval_request``,
 * ``approval_update``, ``pong``, ``session_title_updated``) are forwarded to
 * the optional ``onControlEvent`` callback so a sibling control-plane hook
 * can consume them on the same socket.
 */

import {
  openSessionWs,
  sendCancel,
  sendResume,
  sendUserMessage,
  type ChatMode,
  type SessionEvent,
} from "@/lib/ws";

import type { ChatTransport, UIMessage, UIMessageChunk } from "ai";

/** WS frames that don't belong to the chat data plane. */
export type ControlEvent = Extract<
  SessionEvent,
  | { type: "approval_request" }
  | { type: "approval_update" }
  | { type: "session_title_updated" }
  | { type: "system" }
  | { type: "resume_ack" }
  | { type: "pong" }
>;

export interface SenHarnessTransportOptions {
  /** Stable session id; the WS endpoint is keyed off this. */
  sessionId: string;
  /** Composer mode picked by the user (Plan / Thinking / Flash / …). */
  mode?: ChatMode | null;
  /** Per-turn ``provider:model`` override picked by the ModelSelector. */
  model?: string | null;
  /** Pending attachment ids the next user_message frame should ferry. */
  attachmentIds?: string[];
  /** Optional sink for non-chat frames (approvals, title, pong). */
  onControlEvent?: (event: ControlEvent) => void;
  /** Called with the active ``run_id`` so the caller can wire HITL by-run filters. */
  onRunIdAssigned?: (runId: string | null) => void;
  /** Lifecycle hooks — let the chat session page mirror real socket
   *  state into the control store so the WorkspacePanel status dot can
   *  render a true three-phase (connecting / open / closed) indicator
   *  instead of a hard-coded green pip. Fired once per socket; the
   *  underlying ``openSessionWs`` already de-duplicates listeners. */
  onSocketOpening?: () => void;
  onSocketOpen?: () => void;
  onSocketClose?: () => void;
  onSocketError?: () => void;
}

/**
 * Implements the AI SDK's ``ChatTransport`` against a SenHarness session WS.
 *
 * One transport instance per session. ``sendMessages`` opens (or reuses) the
 * socket via ``ensureSocket()`` — subsequent turns reuse the same WS so the
 * backend skips the per-turn handshake (auth / lost_runs lookup / approval
 * subscribe). The active SDK stream controller lives on ``streamController``
 * and is swapped on each turn; ``dispose()`` is called by the chat page on
 * unmount to close the socket.
 */
export class SenHarnessWsTransport
  implements ChatTransport<UIMessage>
{
  private ws: WebSocket | null = null;
  // Promise that resolves to the live WS once it is OPEN. Held while a
  // handshake is in flight so concurrent ``sendMessages`` / ``reconnect``
  // calls share the same socket instead of racing two opens.
  private wsOpening: Promise<WebSocket> | null = null;
  private currentRunId: string | null = null;
  // Active SDK controller for the in-flight turn. Frames keep flowing
  // into it until ``final`` / ``error`` closes the stream; between turns
  // the socket stays open but ``streamController`` is ``null``.
  private streamController:
    | ReadableStreamDefaultController<UIMessageChunk>
    | null = null;
  private disposed = false;

  // Highest ``seq`` we've observed on this connection. Updated on every
  // server frame that carries one (most run-related frames). On a transient
  // reconnect we send ``resume`` with this value so the server replays
  // whatever we missed.
  private lastSeenSeq = 0;

  private readonly opts: SenHarnessTransportOptions;

  constructor(opts: SenHarnessTransportOptions) {
    this.opts = opts;
  }

  /** Replace the latest mode hint (mutated by the composer between turns). */
  setMode(mode: ChatMode | null): void {
    this.opts.mode = mode;
  }

  /** Replace the per-turn model override (cleared after each successful send). */
  setModel(model: string | null): void {
    this.opts.model = model;
  }

  /** Set the attachment ids the next ``user_message`` will carry. */
  setAttachmentIds(ids: string[] | undefined): void {
    this.opts.attachmentIds = ids;
  }

  /** Surface the live socket so a sibling control-plane hook can share it. */
  getSocket(): WebSocket | null {
    return this.ws;
  }

  /** Highest server-assigned ``seq`` observed so far. Useful for tests / debugging. */
  getLastSeenSeq(): number {
    return this.lastSeenSeq;
  }

  /** True after ``dispose()`` — callers must allocate a fresh transport. */
  isDisposed(): boolean {
    return this.disposed;
  }

  /** Reset the resume cursor — call after the user opens a new chat. */
  resetSeq(): void {
    this.lastSeenSeq = 0;
  }

  /**
   * Open (or return) a connected session WebSocket. Idempotent — repeated
   * calls while a previous open is in flight share the same Promise so we
   * never spin up two concurrent sockets for the same session. Frames
   * route to whichever ``streamController`` is currently bound; if none
   * is bound the dispatch is a no-op (the SDK simply has no active turn
   * to feed).
   */
  private ensureSocket(): Promise<WebSocket> {
    if (this.disposed) {
      return Promise.reject(new Error("transport disposed"));
    }
    if (this.ws && this.ws.readyState === WebSocket.OPEN) {
      return Promise.resolve(this.ws);
    }
    if (this.wsOpening) {
      return this.wsOpening;
    }
    this.opts.onSocketOpening?.();
    this.wsOpening = new Promise<WebSocket>((resolve, reject) => {
      const ws = openSessionWs(this.opts.sessionId, {
        onOpen: () => {
          this.opts.onSocketOpen?.();
          resolve(ws);
        },
        onEvent: (event) => {
          const controller = this.streamController;
          if (!controller) return;
          try {
            dispatch(event, controller, this);
          } catch (err) {
            console.warn("ws.dispatch error:", err);
          }
        },
        onClose: () => {
          // ``final`` normally closes the controller before the socket
          // ever shuts down; this branch fires for unclean disconnects
          // mid-stream or after ``dispose()``.
          const controller = this.streamController;
          if (controller) {
            try {
              controller.close();
            } catch {
              /* already closed */
            }
            this.streamController = null;
          }
          this.ws = null;
          this.wsOpening = null;
          if (this.currentRunId) {
            this.currentRunId = null;
            this.opts.onRunIdAssigned?.(null);
          }
          this.opts.onSocketClose?.();
        },
        onError: () => {
          const controller = this.streamController;
          if (controller) {
            try {
              controller.enqueue({
                type: "error",
                errorText: "ws.connection_error",
              });
            } catch {
              /* already closed */
            }
          }
          this.opts.onSocketError?.();
          if (ws.readyState !== WebSocket.OPEN) {
            reject(new Error("ws.connection_error"));
          }
        },
      });
      this.ws = ws;
    }).finally(() => {
      this.wsOpening = null;
    });
    return this.wsOpening;
  }

  /**
   * Tear the transport down. Closes the active socket (if any) and
   * prevents future ``sendMessages`` calls from re-opening it. Called by
   * the chat session page on unmount so the workspace doesn't accrue
   * idle sockets after the user navigates away.
   */
  dispose(): void {
    this.disposed = true;
    const ws = this.ws;
    this.ws = null;
    this.wsOpening = null;
    const controller = this.streamController;
    this.streamController = null;
    if (controller) {
      try {
        controller.close();
      } catch {
        /* already closed */
      }
    }
    if (ws && ws.readyState !== WebSocket.CLOSED) {
      try {
        ws.close();
      } catch {
        /* ignore close errors */
      }
    }
  }

  // ─── ChatTransport ─────────────────────────────────────────
  async sendMessages(
    options: Parameters<ChatTransport<UIMessage>["sendMessages"]>[0],
  ): Promise<ReadableStream<UIMessageChunk>> {
    const lastUserMessage = [...options.messages]
      .reverse()
      .find((m) => m.role === "user");
    const text = stringifyText(lastUserMessage);

    return new ReadableStream<UIMessageChunk>({
      start: async (controller) => {
        this.streamController = controller;
        let ws: WebSocket;
        try {
          ws = await this.ensureSocket();
        } catch (err) {
          try {
            controller.enqueue({
              type: "error",
              errorText: "ws.connection_error",
            });
            controller.close();
          } catch {
            /* already closed */
          }
          this.streamController = null;
          console.warn("ws.ensureSocket error:", err);
          return;
        }
        // Honour cancellation requests from the SDK (``stop()`` flips this).
        options.abortSignal?.addEventListener("abort", () => {
          if (ws.readyState === WebSocket.OPEN) {
            sendCancel(ws, this.currentRunId ?? undefined);
          }
        });
        sendUserMessage(ws, text, {
          attachmentIds: this.opts.attachmentIds,
          mode: this.opts.mode ?? undefined,
          model: this.opts.model ?? undefined,
        });
        // Caller should reset attachments after successful send. Model
        // override is *sticky* across turns — the composer clears it
        // explicitly via setModel(null) when the user wants to revert.
        this.opts.attachmentIds = undefined;
      },
    });
  }

  /**
   * Re-open the WS for a previously-active session and ask the server to
   * replay anything we missed since ``lastSeenSeq``. Called by the
   * ``useSessionControl`` hook on transient disconnect.
   *
   * Returns ``null`` to keep the AI SDK in its idle state — the missing
   * frames stream back through the existing controller via the original
   * ``sendMessages`` ReadableStream path. We do NOT spin up a second
   * stream; the SDK's resume is implicit (the parts re-arrive as if the
   * disconnect never happened).
   */
  async reconnectToStream(): Promise<ReadableStream<UIMessageChunk> | null> {
    if (!this.lastSeenSeq) {
      // Nothing to replay yet — first message of the chat. Caller should
      // just send the user message normally.
      return null;
    }
    try {
      const ws = await this.ensureSocket();
      sendResume(ws, this.lastSeenSeq, this.currentRunId ?? undefined);
    } catch (err) {
      console.warn("ws.reconnect error:", err);
    }
    // The replay arrives through the active per-turn stream controller
    // (if any). No second stream — SDK resume is implicit.
    return null;
  }
}

// ─── Helpers ────────────────────────────────────────────────

/** Pull a flat string out of the most-recent user message's parts. */
function stringifyText(message: UIMessage | undefined): string {
  if (!message) return "";
  const parts = (message.parts ?? []) as Array<{ type: string; text?: string }>;
  return parts
    .filter((p) => p.type === "text" && typeof p.text === "string")
    .map((p) => p.text!)
    .join("");
}

/**
 * Translate a SenHarness ``SessionEvent`` into one or more ``UIMessageChunk``
 * frames and push them into the SDK controller.
 *
 * Mutates ``transport.currentRunId`` when a ``final`` arrives and forwards
 * non-chat frames to ``onControlEvent``. Also tracks ``lastSeenSeq`` and
 * the active ``run_id`` so reconnects can request the right replay window.
 */
function dispatch(
  event: SessionEvent,
  controller: ReadableStreamDefaultController<UIMessageChunk>,
  transport: SenHarnessWsTransport,
): void {
  // ── Track seq + run_id for reconnect bookkeeping. Bare ``pong`` and
  // ``resume_ack`` carry no seq — they're transient acknowledgements that
  // would otherwise create gaps in the cursor.
  if (event.type !== "pong" && event.type !== "resume_ack") {
    const env = (event as { data?: { seq?: number; run_id?: string } }).data;
    const seq = env?.seq;
    if (typeof seq === "number" && seq > 0) {
      const t = transport as unknown as { lastSeenSeq: number };
      if (seq > t.lastSeenSeq) t.lastSeenSeq = seq;
    }
    const wsRunId = env?.run_id;
    if (typeof wsRunId === "string" && wsRunId) {
      const t = transport as unknown as { currentRunId: string | null };
      if (t.currentRunId !== wsRunId) {
        t.currentRunId = wsRunId;
        transport["opts"].onRunIdAssigned?.(wsRunId);
      }
    }
  }

  switch (event.type) {
    case "delta": {
      // The SDK requires text-start before any text-delta with the same id.
      // We use a single id per run for the assistant text bubble; if we
      // haven't emitted ``text-start`` yet, do it lazily.
      ensureTextStart(controller, transport);
      markHadContent(transport);
      controller.enqueue({
        type: "text-delta",
        id: textIdFor(transport),
        delta: event.data.text,
      });
      break;
    }
    case "thinking": {
      ensureReasoningStart(controller, transport);
      markHadContent(transport);
      controller.enqueue({
        type: "reasoning-delta",
        id: reasoningIdFor(transport),
        delta: event.data.text,
      });
      break;
    }
    case "tool_call": {
      const { id, name, args } = event.data;
      markHadContent(transport);
      controller.enqueue({
        type: "tool-input-start",
        toolCallId: id,
        toolName: name,
      });
      controller.enqueue({
        type: "tool-input-available",
        toolCallId: id,
        toolName: name,
        input: args ?? {},
      });
      break;
    }
    case "tool_result": {
      markHadContent(transport);
      controller.enqueue({
        type: "tool-output-available",
        toolCallId: event.data.id,
        output: event.data.result,
        // Truncated payloads are still "available" but the UI should hint at it.
        ...(event.data.truncated
          ? { providerExecuted: false, dynamic: true }
          : {}),
      });
      break;
    }
    case "usage": {
      // Custom data-part. The matching ``UIMessage`` data type is registered
      // in ``messages.ts`` so ``useChat`` users can read it as ``data-usage``.
      controller.enqueue({
        type: "data-usage",
        data: event.data,
      });
      break;
    }
    case "final": {
      // Close the open text/reasoning parts before emitting ``finish``.
      finalizeText(controller, transport);
      finalizeReasoning(controller, transport);
      // Slash commands surface feedback through ``system`` frames (toasts);
      // they emit no chat content. Suppress message-metadata + finish in
      // that case so we don't render an empty assistant bubble with
      // copy/regenerate affordances on it.
      if (hadContent(transport)) {
        controller.enqueue({
          type: "message-metadata",
          messageMetadata: {
            message_id: event.data.message_id,
            summary: event.data.summary ?? null,
            reason: event.data.reason ?? null,
          },
        });
        controller.enqueue({ type: "finish" });
      }
      resetHadContent(transport);
      controller.close();
      // Detach the controller so the persistent socket doesn't try to
      // push the *next* turn's frames into this finished stream. The
      // next ``sendMessages`` binds a fresh controller.
      const t = transport as unknown as {
        streamController:
          | ReadableStreamDefaultController<UIMessageChunk>
          | null;
      };
      if (t.streamController === controller) {
        t.streamController = null;
      }
      const previous = transport["currentRunId"];
      transport["currentRunId"] = null;
      if (previous) {
        transport["opts"].onRunIdAssigned?.(null);
      }
      break;
    }
    case "error": {
      finalizeText(controller, transport);
      finalizeReasoning(controller, transport);
      resetHadContent(transport);
      // The AI SDK's ``error`` chunk only carries a single ``errorText``
      // string. We pack both the stable ``code`` (for friendly mapping)
      // and the raw backend ``message`` (so users can see what actually
      // went wrong) as a JSON envelope; ``friendlyErrorMessage`` parses
      // it back. Falls back to a plain string when neither is present.
      controller.enqueue({
        type: "error",
        errorText: encodeErrorText(event.data.code, event.data.message),
      });
      // Detach the controller — the SDK will unwind the stream on its
      // side; we must not keep forwarding socket frames into a stream
      // the SDK considers errored. Next turn rebinds via sendMessages.
      const t = transport as unknown as {
        streamController:
          | ReadableStreamDefaultController<UIMessageChunk>
          | null;
      };
      if (t.streamController === controller) {
        t.streamController = null;
      }
      break;
    }
    case "approval_request":
    case "approval_update":
    case "session_title_updated":
    case "system":
    case "resume_ack":
    case "pong": {
      transport["opts"].onControlEvent?.(event as ControlEvent);
      break;
    }
    default: {
      // Unknown frame type — ignore for forward-compat.
      break;
    }
  }
}

// Per-transport "open part" bookkeeping. We intentionally piggy-back on
// transport private fields via bracket access so the ChatTransport public
// surface stays clean.
function textIdFor(transport: SenHarnessWsTransport): string {
  const cached = (transport as unknown as { _textId?: string })._textId;
  if (cached) return cached;
  const id = cryptoRandomId();
  (transport as unknown as { _textId?: string })._textId = id;
  return id;
}

function reasoningIdFor(transport: SenHarnessWsTransport): string {
  const cached = (transport as unknown as { _reasoningId?: string })
    ._reasoningId;
  if (cached) return cached;
  const id = cryptoRandomId();
  (transport as unknown as { _reasoningId?: string })._reasoningId = id;
  return id;
}

function ensureTextStart(
  controller: ReadableStreamDefaultController<UIMessageChunk>,
  transport: SenHarnessWsTransport,
): void {
  const t = transport as unknown as { _textOpen?: boolean };
  if (t._textOpen) return;
  controller.enqueue({ type: "text-start", id: textIdFor(transport) });
  t._textOpen = true;
}

function ensureReasoningStart(
  controller: ReadableStreamDefaultController<UIMessageChunk>,
  transport: SenHarnessWsTransport,
): void {
  const t = transport as unknown as { _reasoningOpen?: boolean };
  if (t._reasoningOpen) return;
  controller.enqueue({
    type: "reasoning-start",
    id: reasoningIdFor(transport),
  });
  t._reasoningOpen = true;
}

function finalizeText(
  controller: ReadableStreamDefaultController<UIMessageChunk>,
  transport: SenHarnessWsTransport,
): void {
  const t = transport as unknown as {
    _textOpen?: boolean;
    _textId?: string;
  };
  if (!t._textOpen) return;
  if (t._textId) {
    controller.enqueue({ type: "text-end", id: t._textId });
  }
  t._textOpen = false;
  t._textId = undefined;
}

function finalizeReasoning(
  controller: ReadableStreamDefaultController<UIMessageChunk>,
  transport: SenHarnessWsTransport,
): void {
  const t = transport as unknown as {
    _reasoningOpen?: boolean;
    _reasoningId?: string;
  };
  if (!t._reasoningOpen) return;
  if (t._reasoningId) {
    controller.enqueue({ type: "reasoning-end", id: t._reasoningId });
  }
  t._reasoningOpen = false;
  t._reasoningId = undefined;
}

function markHadContent(transport: SenHarnessWsTransport): void {
  (transport as unknown as { _hadContent?: boolean })._hadContent = true;
}

function hadContent(transport: SenHarnessWsTransport): boolean {
  return Boolean(
    (transport as unknown as { _hadContent?: boolean })._hadContent,
  );
}

function resetHadContent(transport: SenHarnessWsTransport): void {
  (transport as unknown as { _hadContent?: boolean })._hadContent = false;
}

function cryptoRandomId(): string {
  if (typeof crypto !== "undefined" && "randomUUID" in crypto) {
    return crypto.randomUUID();
  }
  return Math.random().toString(36).slice(2);
}

const ERROR_ENVELOPE_PREFIX = "shx-err:";

function encodeErrorText(
  code: unknown,
  message: unknown,
): string {
  const codeStr = typeof code === "string" && code ? code : "";
  const messageStr = typeof message === "string" && message ? message : "";
  if (!codeStr && !messageStr) return "unknown";
  if (!messageStr) return codeStr;
  return (
    ERROR_ENVELOPE_PREFIX +
    JSON.stringify({ code: codeStr || "unknown", message: messageStr })
  );
}

/**
 * Decode an ``errorText`` produced by ``encodeErrorText``. Returns
 * ``{ code, message }``; ``message`` may be empty when the original
 * frame carried only a code. Tolerates both the JSON envelope and bare
 * legacy strings.
 */
export function decodeErrorText(errorText: string): {
  code: string;
  message: string;
} {
  if (errorText.startsWith(ERROR_ENVELOPE_PREFIX)) {
    try {
      const parsed = JSON.parse(
        errorText.slice(ERROR_ENVELOPE_PREFIX.length),
      ) as { code?: string; message?: string };
      return {
        code: parsed.code ?? "unknown",
        message: parsed.message ?? "",
      };
    } catch {
      // Malformed envelope — fall through.
    }
  }
  return { code: errorText, message: "" };
}
