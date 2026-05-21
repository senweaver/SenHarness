"use client";

import { use, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useTranslations } from "next-intl";
import { useQueryClient } from "@tanstack/react-query";
import { Chat, useChat } from "@ai-sdk/react";
import {
  IconCheck,
  IconCopy,
  IconRefresh,
  IconShare,
} from "@tabler/icons-react";
import { toast } from "sonner";

import { Action, Actions } from "@/components/ai-elements/actions";
import {
  Conversation,
  ConversationContent,
  ConversationScrollButton,
} from "@/components/ai-elements/conversation";
import { Message } from "@/components/ai-elements/message";
import { Reasoning } from "@/components/ai-elements/reasoning";
import { Response } from "@/components/ai-elements/response";
import {
  Suggestion,
  Suggestions,
} from "@/components/ai-elements/suggestion";
import { Tool } from "@/components/ai-elements/tool";
import { AlignmentDot } from "@/components/chat/AlignmentDot";
import {
  ChatInput,
  type ChatInputHandle,
  type ChatInputSubmission,
} from "@/components/chat/ChatInput";
import { RatingButtons } from "@/components/chat/RatingButtons";
import { SessionGoalBanner } from "@/components/chat/SessionGoalBanner";
import { ShareDialog } from "@/components/chat/ShareDialog";
import { api } from "@/lib/api";
import {
  decodeErrorText,
  SenHarnessWsTransport,
  type ControlEvent,
} from "@/lib/chat-transport";
import { switchActiveWorkspace } from "@/lib/workspace";
import { useAuthStore } from "@/stores/auth-store";
import { usePendingPromptStore } from "@/stores/pending-prompt-store";
import { useRenderedMessages } from "@/hooks/use-rendered-messages";
import { useSessionControl } from "@/hooks/use-session-control";
import { useWorkspacePaneStore } from "@/stores/workspace-pane-store";
import { useSessionRatings } from "@/hooks/use-message-rating";
import {
  useActiveSessionGoal,
  useSessionAlignment,
} from "@/hooks/use-session-goals";
import { useSessionSuggestions } from "@/hooks/use-session-suggestions";
import { useAgent } from "@/hooks/use-agent-mutations";
import type {
  GoalAlignmentScoreRead,
  MessageRead,
  MessageRatingSummary,
  SessionRead,
} from "@/types/api";
import type { UIMessage } from "ai";
import type { StickToBottomContext } from "use-stick-to-bottom";

/** Decode the `ws` claim from a JWT without verifying the signature. */
function readTokenWorkspace(token: string | null): string | null {
  if (!token) return null;
  const parts = token.split(".");
  const body = parts[1];
  if (!body) return null;
  try {
    const json = atob(body.replace(/-/g, "+").replace(/_/g, "/"));
    const payload = JSON.parse(json) as { ws?: string };
    return typeof payload.ws === "string" ? payload.ws : null;
  } catch {
    return null;
  }
}

function friendlyErrorMessage(
  tr: (k: string, vars?: Record<string, string>) => string,
  code: string,
  reason?: string,
): string {
  switch (code) {
    case "rate_limit.exceeded":
      return tr("errorRateLimited");
    case "agent.budget_exceeded":
    case "budget_exceeded":
      return tr("errorBudgetExceeded");
    case "shields.blocked":
      return tr("errorShieldBlocked");
    case "auth.forbidden":
    case "forbidden":
      return tr("errorForbidden");
    case "agent.iteration_budget_exhausted":
      return tr("errorIterationExhausted");
    case "model.unavailable":
    case "provider.unavailable":
      return tr("errorModelUnavailable");
    case "ws.connection_error":
    case "ws.reconnect_error":
    case "ws.parse_error":
      return tr("errorConnection");
    case "stuck_loop":
      return tr("compose.stuckLoop");
    default: {
      // Surface the underlying reason when the backend supplies one
      // (e.g. a model HTTP 400 from the provider). Without this the
      // user only sees "Something went wrong" and has no way to
      // self-diagnose. Cap the length so a runaway stack trace doesn't
      // overflow the toast.
      const trimmed = reason?.trim();
      if (trimmed) {
        const capped =
          trimmed.length > 240 ? `${trimmed.slice(0, 240)}…` : trimmed;
        return tr("errorWithReason", { reason: capped });
      }
      return tr("errorGeneric");
    }
  }
}

function buildInitialMessages(history: MessageRead[]): UIMessage[] {
  const out: UIMessage[] = [];
  for (const m of history) {
    if (m.role !== "user" && m.role !== "assistant") continue;
    const text = (m.content_json as { text?: string })?.text ?? "";
    if (!text && (!m.tool_call_json || m.role !== "assistant")) continue;

    const parts: UIMessage["parts"] = [];
    if (text) parts.push({ type: "text", text } as UIMessage["parts"][number]);

    if (m.role === "assistant" && m.tool_call_json) {
      const events = Array.isArray(
        (m.tool_call_json as { events?: unknown[] }).events,
      )
        ? ((m.tool_call_json as { events: unknown[] }).events as Array<
            Record<string, unknown>
          >)
        : [];
      const calls = new Map<
        string,
        {
          id: string;
          name: string;
          args: Record<string, unknown>;
          result?: unknown;
        }
      >();
      for (const ev of events) {
        if (typeof ev.id !== "string") continue;
        if (typeof ev.name === "string") {
          calls.set(ev.id, {
            id: ev.id,
            name: ev.name,
            args:
              typeof ev.args === "object" && ev.args !== null
                ? (ev.args as Record<string, unknown>)
                : {},
          });
        } else if ("result" in ev) {
          const existing = calls.get(ev.id);
          if (existing) existing.result = ev.result;
        }
      }
      for (const c of calls.values()) {
        parts.push({
          type: `tool-${c.name}`,
          toolCallId: c.id,
          state: c.result !== undefined ? "output-available" : "input-available",
          input: c.args,
          output: c.result,
        } as unknown as UIMessage["parts"][number]);
      }
    }

    out.push({
      id: m.id,
      role: m.role,
      parts,
    } as UIMessage);
  }
  return out;
}

const UUID_RE =
  /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

export default function ChatSessionPage({
  params,
}: {
  params: Promise<{ sessionId: string }>;
}) {
  const { sessionId } = use(params);
  const t = useTranslations("chat");
  const tCompose = useTranslations("chat");
  const qc = useQueryClient();
  const consumePending = usePendingPromptStore((s) => s.consume);
  const inputRef = useRef<ChatInputHandle>(null);
  const openWorkspaceTab = useWorkspacePaneStore((s) => s.openTab);
  // ID of the assistant message the user clicked Share on. The deep-link in
  // the ShareDialog points at the session, but we surface it via the same
  // primitive so the per-message Share button feels native.
  const [shareTargetMessageId, setShareTargetMessageId] = useState<string | null>(null);

  // ─── 1) Workspace preflight + session subject capture ───
  const [wsReady, setWsReady] = useState(false);
  // Captured from the session preflight so the chat composer can pull
  // per-agent skills (`/` palette) without a second round-trip.
  const [subjectAgentId, setSubjectAgentId] = useState<string | null>(null);
  useEffect(() => {
    let cancelled = false;
    setWsReady(false);
    (async () => {
      try {
        const ses = await api.get<SessionRead>(`/api/v1/sessions/${sessionId}`);
        if (cancelled) return;
        if (ses.kind === "p2p") {
          setSubjectAgentId(ses.subject_id ?? null);
        } else {
          setSubjectAgentId(null);
        }
        const tokenWs = readTokenWorkspace(useAuthStore.getState().accessToken);
        if (ses.workspace_id !== tokenWs) {
          const ok = await switchActiveWorkspace(ses.workspace_id);
          if (cancelled) return;
          if (!ok) {
            toast.error(t("loadHistoryFailed"));
            return;
          }
        }
        if (!cancelled) setWsReady(true);
      } catch (err) {
        if (!cancelled) {
          console.warn("session preflight failed:", err);
          setWsReady(true);
        }
      }
    })();
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sessionId]);

  // ─── 2) Wire control plane bridges (stable across renders). ───
  const control = useSessionControl(sessionId);
  const onControlEventRef = useRef(control.onControlEvent);
  onControlEventRef.current = control.onControlEvent;
  const onRunIdAssignedRef = useRef(control.onRunIdAssigned);
  onRunIdAssignedRef.current = control.onRunIdAssigned;
  // Refs so the transport sees the latest setter even if React hasn't
  // re-built the transport (which it doesn't — see ``useMemo`` below).
  const setWsPhaseRef = useRef(control.setWsPhase);
  setWsPhaseRef.current = control.setWsPhase;

  // ─── 3) Session transport (survives across turns, recreated on dispose). ───
  // React Strict Mode / Fast Refresh run effect cleanup then remount with the
  // same ``useMemo`` cache — a disposed transport must not be reused.
  const transportRef = useRef<SenHarnessWsTransport | null>(null);
  const transportSessionRef = useRef(sessionId);

  if (
    transportSessionRef.current !== sessionId ||
    !transportRef.current ||
    transportRef.current.isDisposed()
  ) {
    transportRef.current?.dispose();
    transportSessionRef.current = sessionId;
    transportRef.current = new SenHarnessWsTransport({
      sessionId,
      mode: "flash",
      onControlEvent: (ev: ControlEvent) => onControlEventRef.current(ev),
      onRunIdAssigned: (id) => onRunIdAssignedRef.current(id),
      onSocketOpening: () => setWsPhaseRef.current("connecting"),
      onSocketOpen: () => setWsPhaseRef.current("open"),
      onSocketClose: () => setWsPhaseRef.current("closed"),
      onSocketError: () => setWsPhaseRef.current("error"),
    });
  }
  const transport = transportRef.current;

  useEffect(() => {
    const instance = transportRef.current;
    return () => {
      instance?.dispose();
      if (transportRef.current === instance) {
        transportRef.current = null;
      }
    };
  }, [sessionId]);

  // ─── 4) Bind a Chat instance — must be created exactly once per session. ───
  const chat = useMemo(
    () => new Chat<UIMessage>({ id: sessionId, transport, messages: [] }),
    [sessionId, transport],
  );

  const {
    messages: rawMessages,
    status,
    sendMessage,
    regenerate,
    stop,
    error,
    setMessages,
  } = useChat<UIMessage>({ chat });

  const messages = useRenderedMessages(
    rawMessages,
    status === "streaming" || status === "submitted",
  );

  // ─── 5) Hydrate history once per session via the SDK ``setMessages`` ───
  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const msgs = await api.get<MessageRead[]>(
          `/api/v1/sessions/${sessionId}/messages`,
        );
        if (cancelled) return;
        const initial = buildInitialMessages(msgs);
        if (initial.length > 0) setMessages(initial);
      } catch (err) {
        if (!cancelled) {
          toast.error(t("loadHistoryFailed"));
          console.warn("chat history load failed:", err);
        }
      }
    })();
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sessionId]);

  useEffect(() => {
    if (!error) return;
    const raw = (error as { errorText?: string }).errorText ?? error.message;
    if (!raw) return;
    const { code, message } = decodeErrorText(raw);
    toast.error(friendlyErrorMessage(t, code, message));
  }, [error, t]);

  // ─── Auto-scroll glue ─────────────────────────────────────
  // ``use-stick-to-bottom`` only auto-follows new content while the user
  // is already pinned to the bottom (``isAtBottom = true``). Submitting
  // a turn while scrolled up — common when re-reading an earlier reply —
  // would otherwise leave the new user bubble + streamed assistant tokens
  // entirely below the viewport, so the chat *appears* frozen until the
  // user manually clicks the scroll button. We hold the live
  // ``StickToBottomContext`` and call ``scrollToBottom()`` on every send
  // to re-engage the lock for that turn.
  const conversationCtxRef = useRef<StickToBottomContext | null>(null);
  const scrollChatToBottom = useCallback(() => {
    const ctx = conversationCtxRef.current;
    if (!ctx) return;
    try {
      ctx.scrollToBottom();
    } catch (err) {
      console.warn("scrollToBottom failed:", err);
    }
  }, []);

  // ─── 6) Auto-fire pending prompt from /chat/new ───
  const firedRef = useRef(false);
  useEffect(() => {
    if (firedRef.current || !wsReady) return;
    firedRef.current = true;
    const pending = consumePending(sessionId);
    if (!pending) return;
    const text = pending.webSearch
      ? `[web_search] ${pending.text}`.trim()
      : pending.text;
    if (pending.attachments?.length) {
      transport.setAttachmentIds(pending.attachments.map((a) => a.id));
    }
    if (pending.mode) transport.setMode(pending.mode);
    if (pending.model !== undefined) transport.setModel(pending.model);
    sendMessage({ text });
    scrollChatToBottom();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [wsReady]);

  const ratingsQ = useSessionRatings(sessionId);
  const ratingMap = useMemo(() => {
    const map = new Map<string, MessageRatingSummary>();
    for (const r of ratingsQ.data ?? []) map.set(r.message_id, r);
    return map;
  }, [ratingsQ.data]);

  // M0.1 — alignment dots per assistant message. Latest score wins
  // (the API returns rows ordered by created_at ASC; we walk forward
  // and overwrite so the final entry is the most recent).
  const goalQ = useActiveSessionGoal(sessionId);
  const alignmentQ = useSessionAlignment(sessionId);
  const alignmentMap = useMemo(() => {
    const map = new Map<string, GoalAlignmentScoreRead>();
    for (const row of alignmentQ.data ?? []) map.set(row.message_id, row);
    return map;
  }, [alignmentQ.data]);
  const alignmentThreshold = goalQ.data?.alignment_threshold ?? 0.6;

  // ─── Follow-up suggestions ───────────────────────────────────
  // Opt-in via Agent.metadata_json.chat_features.suggestions_enabled.
  // We mutate the suggestion list when the latest run finalises and
  // the agent has the feature flipped on. Backend re-checks the same
  // flag so a stale flag flip can't sneak through.
  const agentQ = useAgent(subjectAgentId);
  const suggestionsEnabled = Boolean(
    (agentQ.data?.metadata_json as { chat_features?: { suggestions_enabled?: boolean } } | null | undefined)
      ?.chat_features?.suggestions_enabled,
  );
  const [suggestions, setSuggestions] = useState<string[]>([]);
  const suggestionsM = useSessionSuggestions();
  const lastSuggestionRunId = useRef<string | null>(null);
  useEffect(() => {
    if (!suggestionsEnabled) {
      setSuggestions([]);
      return;
    }
    if (status !== "ready" || !messages.length) return;
    const lastAssistant = [...messages]
      .reverse()
      .find((m) => m.role === "assistant");
    if (!lastAssistant) return;
    if (lastSuggestionRunId.current === lastAssistant.id) return;
    lastSuggestionRunId.current = lastAssistant.id;
    setSuggestions([]);
    suggestionsM.mutate(sessionId, {
      onSuccess: (result) => {
        if (Array.isArray(result)) setSuggestions(result.slice(0, 5));
      },
      onError: () => setSuggestions([]),
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [status, messages, sessionId, suggestionsEnabled]);

  const send = useCallback(
    (submission: ChatInputSubmission) => {
      transport.setMode(submission.mode);
      // Per-turn ``provider:model`` override (or null = use saved pref /
      // agent default). The transport forwards it on the next user_message
      // frame; the backend reads it as ``RunRequest.model_override``.
      transport.setModel(submission.model);
      transport.setAttachmentIds(
        submission.attachments.length
          ? submission.attachments.map((a) => a.id)
          : undefined,
      );
      sendMessage({ text: submission.text });
      // Re-pin the viewport to the new turn before the assistant tokens
      // start streaming back. Without this the user sees the chat appear
      // to freeze for as long as the model thinks, then the entire
      // response materialises "all at once" when they manually scroll.
      scrollChatToBottom();
      // Clear stale suggestions immediately — the next round of chips
      // will be regenerated against the new turn.
      setSuggestions([]);
    },
    [transport, sendMessage, scrollChatToBottom],
  );

  // Bump the recent-sessions query whenever a turn finalises. The WS path
  // mutates ``last_message_at`` / ``message_count`` on the backend but never
  // invalidates the React-Query cache, so without this nudge the active
  // session stays where it was in the left rail (often *not* at the top)
  // until the next refetch. We trigger on the ``streaming|submitted → ready``
  // transition so we don't spam the network during long streams.
  const prevStatusRef = useRef<typeof status | null>(null);
  useEffect(() => {
    const prev = prevStatusRef.current;
    prevStatusRef.current = status;
    if (prev === status) return;
    if (
      status === "ready" &&
      (prev === "streaming" || prev === "submitted")
    ) {
      qc.invalidateQueries({ queryKey: ["sessions", "recent"] });
      qc.invalidateQueries({ queryKey: ["sessions", "detail"] });
    }
  }, [status, qc]);

  const onCancel = useCallback(() => {
    stop();
  }, [stop]);

  const onRegenerate = useCallback(() => {
    regenerate();
    // ``regenerate`` mutates the latest assistant turn in place; without
    // re-pinning, the viewport drifts away from the response just like
    // ``send`` would. Keep parity with the user-submit path so both
    // entry points behave the same way.
    scrollChatToBottom();
  }, [regenerate, scrollChatToBottom]);

  // Auto-open workspace tabs on certain tool events.
  useEffect(() => {
    if (!messages.length) return;
    const lastAssistant = [...messages].reverse().find((m) => m.role === "assistant");
    if (!lastAssistant) return;
    for (const part of lastAssistant.parts ?? []) {
      const type = (part as { type?: string }).type ?? "";
      if (
        type === "tool-write_file" ||
        type === "tool-edit_file" ||
        type === "tool-generate_image"
      ) {
        const toolCallId = (part as { toolCallId?: string }).toolCallId;
        openWorkspaceTab("files", { artifactId: toolCallId });
        return;
      }
      if (type === "tool-write_todos") {
        openWorkspaceTab("plan");
        return;
      }
      // ``shell`` is the most dangerous tool — surface its output the
      // moment the model invokes it so the user can audit live without
      // hunting for the Terminal tab.
      if (type === "tool-shell") {
        openWorkspaceTab("terminal");
        return;
      }
    }
  }, [messages, openWorkspaceTab]);

  // Bind the live transport socket to the control hook. The hook attaches
  // its own ``close`` listener that auto-refreshes the access token on
  // ``WS_CLOSE.AUTH_EXPIRED`` (4401) so this component never imports the
  // raw ``WS_CLOSE`` constant from ``lib/ws.ts`` — it stays the sole
  // domain of the control plane + transport adapter.
  useEffect(() => {
    control.bindSocket(transport.getSocket());
    return () => control.bindSocket(null);
  }, [transport, status, control]);

  const promptStatus =
    status === "streaming"
      ? "streaming"
      : status === "submitted"
        ? "submitted"
        : status === "error"
          ? "error"
          : "ready";

  return (
    <div className="flex h-full min-h-0 flex-col">
      <SessionGoalBanner sessionId={sessionId} />
      <Conversation contextRef={conversationCtxRef}>
        <ConversationContent>
          {messages.map((m, mi) => {
            const isLast = mi === messages.length - 1;
            const isStreamingThis =
              isLast && status === "streaming" && m.role === "assistant";
            return (
              <Message key={m.id} role={m.role}>
                {(m.parts ?? []).map((part, idx) => {
                  const type = (part as { type?: string }).type ?? "";
                  if (type === "text") {
                    const text = (part as { text?: string }).text ?? "";
                    return (
                      <Response
                        key={idx}
                        id={`${m.id}:${idx}`}
                        streaming={isStreamingThis && m.role === "assistant"}
                      >
                        {text}
                      </Response>
                    );
                  }
                  if (type === "reasoning") {
                    const text = (part as { text?: string }).text ?? "";
                    return (
                      <Reasoning
                        key={idx}
                        streaming={isStreamingThis}
                        labels={{
                          streaming: tCompose("compose.thinkingActive"),
                          finished: (s) =>
                            tCompose("compose.thinkingFor", { seconds: s }),
                        }}
                      >
                        {text}
                      </Reasoning>
                    );
                  }
                  if (type.startsWith("tool-") || type === "dynamic-tool") {
                    const tp = part as {
                      type: string;
                      toolName?: string;
                      toolCallId?: string;
                      state?:
                        | "input-streaming"
                        | "input-available"
                        | "output-available"
                        | "output-error";
                      input?: unknown;
                      output?: unknown;
                      errorText?: string;
                    };
                    const toolName =
                      tp.toolName ?? type.replace(/^tool-/, "");
                    return (
                      <Tool
                        key={tp.toolCallId ?? idx}
                        toolCallId={tp.toolCallId ?? `${m.id}-${idx}`}
                        toolName={toolName}
                        state={tp.state ?? "output-available"}
                        input={tp.input}
                        output={tp.output}
                        errorText={tp.errorText}
                      />
                    );
                  }
                  return null;
                })}
                {m.role === "assistant" && !isStreamingThis && (
                  <Actions className="opacity-0 transition-opacity group-hover:opacity-100 group-focus-within:opacity-100">
                    {UUID_RE.test(m.id) ? (
                      <RatingButtons
                        sessionId={sessionId}
                        messageId={m.id}
                        summary={ratingMap.get(m.id)}
                      />
                    ) : null}
                    <CopyMessageAction message={m} t={tCompose} />
                    <Action
                      label={tCompose("compose.regenerate")}
                      icon={<IconRefresh />}
                      onClick={onRegenerate}
                    />
                    {UUID_RE.test(m.id) ? (
                      <Action
                        label={tCompose("compose.shareMessage")}
                        icon={<IconShare />}
                        onClick={() => setShareTargetMessageId(m.id)}
                      />
                    ) : null}
                    {goalQ.data && UUID_RE.test(m.id) ? (
                      <span className="ml-1 inline-flex items-center self-end pb-1">
                        <AlignmentDot
                          score={alignmentMap.get(m.id) ?? null}
                          threshold={alignmentThreshold}
                        />
                      </span>
                    ) : null}
                  </Actions>
                )}
              </Message>
            );
          })}
        </ConversationContent>
        <ConversationScrollButton />
      </Conversation>

      {suggestions.length > 0 && status === "ready" ? (
        <div className="mx-auto w-full max-w-3xl shrink-0 px-3 pb-1 sm:px-6">
          <Suggestions>
            {suggestions.map((s) => (
              <Suggestion
                key={s}
                onClick={() => {
                  sendMessage({ text: s });
                  scrollChatToBottom();
                }}
              >
                {s}
              </Suggestion>
            ))}
          </Suggestions>
        </div>
      ) : null}

      {/* No footer status strip — the trace replay now renders inline in
          the right-rail Workspace panel (Replay tab), and share lives in
          that same header. Anything that previously deep-linked into
          ``/traces/<id>`` is one tab-click away inside the conversation. */}

      {/* Per-message share dialog. Hidden trigger; the assistant-message
          Action above flips ``shareTargetMessageId`` to drive ``open``. The
          dialog itself talks to the session-wide share API; we treat a
          per-message share as a session share with focused intent for v1. */}
      {shareTargetMessageId !== null && (
        <ShareDialog
          key={shareTargetMessageId}
          sessionId={sessionId}
          trigger={null}
          open
          onOpenChange={(o) => {
            if (!o) setShareTargetMessageId(null);
          }}
        />
      )}

      <div className="shrink-0">
        <ChatInput
          ref={inputRef}
          sessionId={sessionId}
          agentId={subjectAgentId}
          status={promptStatus}
          isConnected={wsReady}
          onSend={send}
          onCancel={onCancel}
          onRegenerate={onRegenerate}
        />
      </div>
    </div>
  );
}

/**
 * Small wrapper around the AI Elements `Action` chip that copies the
 * concatenated text of an assistant message to the clipboard. Reuses the
 * action toolbar's icon-only chrome and falls back to ``document.execCommand``
 * when ``navigator.clipboard`` isn't available (e.g. dev over plain HTTP).
 */
function CopyMessageAction({
  message,
  t,
}: {
  message: UIMessage;
  t: ReturnType<typeof useTranslations>;
}) {
  const [copied, setCopied] = useState(false);

  const text = useMemo(() => {
    const parts = message.parts ?? [];
    const out: string[] = [];
    for (const p of parts) {
      const tp = p as { type?: string; text?: string };
      if (tp.type === "text" && typeof tp.text === "string") {
        out.push(tp.text);
      }
    }
    return out.join("");
  }, [message]);

  const onCopy = useCallback(async () => {
    if (!text) return;
    try {
      if (navigator.clipboard && window.isSecureContext) {
        await navigator.clipboard.writeText(text);
      } else {
        // Fallback for non-secure contexts (e.g. plain-HTTP dev).
        const ta = document.createElement("textarea");
        ta.value = text;
        ta.style.position = "fixed";
        ta.style.opacity = "0";
        document.body.appendChild(ta);
        ta.select();
        document.execCommand("copy");
        document.body.removeChild(ta);
      }
      setCopied(true);
      window.setTimeout(() => setCopied(false), 2000);
    } catch (err) {
      console.warn("copy message failed:", err);
    }
  }, [text]);

  return (
    <Action
      label={copied ? t("compose.copied") : t("compose.copy")}
      icon={
        copied ? (
          <IconCheck className="text-green-500" />
        ) : (
          <IconCopy />
        )
      }
      onClick={onCopy}
      disabled={!text}
      data-testid="message-copy"
    />
  );
}
