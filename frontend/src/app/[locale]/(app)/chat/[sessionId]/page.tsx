"use client";

import { Link } from "@/lib/navigation";
import { use, useCallback, useEffect, useRef, useState } from "react";
import { useTranslations } from "next-intl";
import { useQueryClient } from "@tanstack/react-query";
import { IconActivity } from "@tabler/icons-react";
import { toast } from "sonner";

import { type AttachmentRef } from "@/components/chat/AttachmentView";
import { ChatHeader } from "@/components/chat/ChatHeader";
import { ChatInput, type ChatInputHandle } from "@/components/chat/ChatInput";
import { MessageList, type Turn } from "@/components/chat/MessageList";
import { ShareDialog } from "@/components/chat/ShareDialog";
import {
  openSessionWs,
  sendApprovalDecision,
  sendCancel,
  sendUserMessage,
  type SessionEvent,
} from "@/lib/ws";
import { usePendingPromptStore } from "@/stores/pending-prompt-store";
import { api } from "@/lib/api";
import type { MessageRead } from "@/types/api";
import { usePermissions } from "@/hooks/use-permissions";

/**
 * `ChatSessionPage` — the live conversation surface.
 *
 * Wires:
 *   - REST `GET /sessions/{id}/messages` for transcript hydration on mount.
 *   - WebSocket `/sessions/ws/{id}` for streaming deltas, tool events, HITL.
 *   - The (newly extracted) `MessageList` / `ChatInput` components for UX.
 *
 * The component owns a `Turn[]` projection of all events. Every WS frame
 * mutates the array in place; presentation lives in the child components.
 */
export default function ChatSessionPage({
  params,
}: {
  params: Promise<{ sessionId: string }>;
}) {
  const { sessionId } = use(params);
  const t = useTranslations("chat");
  const qc = useQueryClient();
  const consumePending = usePendingPromptStore((s) => s.consume);
  const perms = usePermissions();

  // Approval gating: in chat the user is always the requester (they triggered
  // the run), so they get decide_own. Admin/operator with department match
  // also pass through usePermissions.
  const canDecide = perms.canDecideApproval({
    requestedByIdentityId: perms.identityId,
    sessionOwnerIdentityId: perms.identityId,
    sessionOwnerDepartmentId: perms.departmentId,
  });

  const [turns, setTurns] = useState<Turn[]>([]);
  const [streaming, setStreaming] = useState(false);
  const [wsConnected, setWsConnected] = useState(false);
  const [usage, setUsage] = useState<{
    input?: number;
    output?: number;
  } | null>(null);

  const wsRef = useRef<WebSocket | null>(null);
  const streamingIdRef = useRef<string | null>(null);
  const scrollEndRef = useRef<HTMLDivElement | null>(null);
  const inputRef = useRef<ChatInputHandle>(null);

  /**
   * Map common backend error codes to a friendlier i18n string. Falls back
   * to the generic copy; the caller appends `(code)` for ops/debug context.
   */
  const friendlyErrorMessage = useCallback(
    (tr: (k: string) => string, code: string): string => {
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
        default:
          return tr("errorGeneric");
      }
    },
    [],
  );

  const pushUser = useCallback(
    (text: string, attachments?: AttachmentRef[]) => {
      setTurns((prev) => [
        ...prev,
        {
          id: crypto.randomUUID(),
          role: "user",
          text,
          attachments,
          timestamp: new Date().toISOString(),
        },
      ]);
    },
    [],
  );

  // ────────────────────────────────────────────
  // WS event handler — defined before the effect that registers it.
  // ────────────────────────────────────────────
  const handleEvent = useCallback(
    (event: SessionEvent) => {
      switch (event.type) {
        case "delta": {
          const chunk = event.data.text;
          setTurns((prev) => {
            const last = prev[prev.length - 1];
            if (last && last.role === "assistant" && last.streaming) {
              return [
                ...prev.slice(0, -1),
                { ...last, text: (last.text ?? "") + chunk },
              ];
            }
            const id = crypto.randomUUID();
            streamingIdRef.current = id;
            return [
              ...prev,
              { id, role: "assistant", text: chunk, streaming: true },
            ];
          });
          break;
        }
        case "thinking": {
          setTurns((prev) => [
            ...prev,
            { id: crypto.randomUUID(), role: "thinking", text: event.data.text },
          ]);
          break;
        }
        case "tool_call": {
          setTurns((prev) => [
            ...prev,
            {
              id: event.data.id,
              role: "tool_call",
              toolName: event.data.name,
              toolArgs: event.data.args,
              toolStatus: "running",
            },
          ]);
          break;
        }
        case "tool_result": {
          setTurns((prev) =>
            prev.map((turn) =>
              turn.role === "tool_call" && turn.id === event.data.id
                ? {
                    ...turn,
                    toolResult: event.data.result,
                    toolStatus: "completed",
                  }
                : turn,
            ),
          );
          break;
        }
        case "approval_request": {
          setTurns((prev) => [
            ...prev,
            {
              id: `appr-${event.data.id}`,
              role: "approval",
              approvalId: event.data.id,
              toolName: event.data.tool_name,
              toolArgs: event.data.tool_args,
              approvalSummary: event.data.summary,
              approvalExpiresAt: event.data.expires_at,
              approvalStatus: "pending",
            },
          ]);
          break;
        }
        case "approval_update": {
          setTurns((prev) =>
            prev.map((turn) =>
              turn.role === "approval" && turn.approvalId === event.data.id
                ? {
                    ...turn,
                    approvalStatus:
                      event.data.status as Turn["approvalStatus"],
                  }
                : turn,
            ),
          );
          break;
        }
        case "usage": {
          const tokens = event.data.tokens as Record<string, number> | undefined;
          setUsage({ input: tokens?.input, output: tokens?.output });
          break;
        }
        case "final": {
          setStreaming(false);
          // Replace the placeholder UUID we minted for the streaming bubble
          // with the server-issued message_id so RatingButtons can target it.
          const realId = event.data.message_id;
          setTurns((prev) =>
            prev.map((tn) =>
              tn.streaming && tn.id === streamingIdRef.current
                ? {
                    ...tn,
                    id: realId || tn.id,
                    streaming: false,
                    timestamp: new Date().toISOString(),
                  }
                : tn,
            ),
          );
          streamingIdRef.current = null;
          // Refresh session so ChatHeader picks up subject_id after self-heal
          // (backend sets it during the first turn if it was null).
          qc.invalidateQueries({ queryKey: ["sessions", "detail"] });
          break;
        }
        case "error": {
          setStreaming(false);
          const friendly = friendlyErrorMessage(t, event.data.code);
          toast.error(friendly);
          setTurns((prev) => [
            ...prev,
            {
              id: crypto.randomUUID(),
              role: "assistant",
              text: `⚠ ${friendly} (${event.data.code})`,
              timestamp: new Date().toISOString(),
            },
          ]);
          break;
        }
        default:
          break;
      }
    },
    [t, friendlyErrorMessage],
  );

  // ────────────────────────────────────────────
  // Hydrate transcript from REST on mount
  // ────────────────────────────────────────────
  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const msgs = await api.get<MessageRead[]>(
          `/api/v1/sessions/${sessionId}/messages`,
        );
        if (cancelled) return;
        const mapped: Turn[] = [];
        for (const m of msgs) {
          if (m.role !== "user" && m.role !== "assistant") continue;
          const text = (m.content_json as { text?: string })?.text ?? "";
          const attachments = Array.isArray(m.attachments_json)
            ? (m.attachments_json as unknown[]).filter(
                (a): a is AttachmentRef =>
                  typeof a === "object" &&
                  a !== null &&
                  typeof (a as { id?: unknown }).id === "string",
              )
            : undefined;
          mapped.push({
            id: m.id,
            role: m.role as "user" | "assistant",
            text,
            attachments,
            timestamp: m.created_at,
          });
          // Replay tool events recorded on assistant messages so reload still
          // shows the tool cards (they live under `tool_call_json.events`).
          if (m.role === "assistant" && m.tool_call_json) {
            const events = Array.isArray(
              (m.tool_call_json as { events?: unknown[] }).events,
            )
              ? ((m.tool_call_json as { events: unknown[] }).events as Array<
                  Record<string, unknown>
                >)
              : [];
            // Pair tool_call + tool_result by id.
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
              if (typeof ev.id !== "string" || typeof ev.name !== "string") {
                // result event has only `id` + `result`; pair with its call.
                if (typeof ev.id === "string" && "result" in ev) {
                  const existing = calls.get(ev.id);
                  if (existing) existing.result = ev.result;
                }
                continue;
              }
              calls.set(ev.id, {
                id: ev.id,
                name: ev.name,
                args:
                  typeof ev.args === "object" && ev.args !== null
                    ? (ev.args as Record<string, unknown>)
                    : {},
              });
            }
            for (const c of calls.values()) {
              mapped.push({
                id: `${m.id}-tc-${c.id}`,
                role: "tool_call",
                toolName: c.name,
                toolArgs: c.args,
                toolResult: c.result,
                toolStatus: c.result !== undefined ? "completed" : "pending",
              });
            }
          }
        }
        setTurns(mapped);
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

  // ────────────────────────────────────────────
  // Connect WebSocket
  // ────────────────────────────────────────────
  useEffect(() => {
    const ws = openSessionWs(sessionId, {
      onOpen: () => {
        setWsConnected(true);
        const pending = consumePending(sessionId);
        if (pending && (pending.text || (pending.attachments?.length ?? 0) > 0)) {
          // Prepend a lightweight hint so the agent picks up the "use web
          // search" intent the user toggled on the home composer. The hint
          // is human-readable + cheap to ignore if the agent has no web
          // tool wired up.
          const text = pending.webSearch
            ? `[web_search] ${pending.text}`.trim()
            : pending.text;
          const attachments = pending.attachments ?? [];
          pushUser(text, attachments.length ? attachments : undefined);
          sendUserMessage(
            ws,
            text,
            attachments.length ? attachments.map((a) => a.id) : undefined,
          );
          setStreaming(true);
        }
      },
      onEvent: handleEvent,
      onClose: () => {
        setWsConnected(false);
        setStreaming(false);
      },
    });
    wsRef.current = ws;
    return () => {
      ws.close();
      wsRef.current = null;
      setWsConnected(false);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sessionId]);

  // ────────────────────────────────────────────
  // Auto-scroll on new turns
  // ────────────────────────────────────────────
  useEffect(() => {
    scrollEndRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [turns]);

  const send = (text: string, attachments: AttachmentRef[]) => {
    if (
      !wsRef.current ||
      wsRef.current.readyState !== WebSocket.OPEN ||
      (!text && attachments.length === 0)
    ) {
      if (!wsRef.current || wsRef.current.readyState !== WebSocket.OPEN) {
        toast.error(t("wsDisconnected"));
      }
      return;
    }
    pushUser(text, attachments.length ? attachments : undefined);
    sendUserMessage(
      wsRef.current,
      text,
      attachments.length ? attachments.map((a) => a.id) : undefined,
    );
    setStreaming(true);
  };

  const cancel = () => {
    if (!wsRef.current || wsRef.current.readyState !== WebSocket.OPEN) return;
    sendCancel(wsRef.current);
    setStreaming(false);
  };

  const onApproveQuick = (approvalId: string) => {
    if (!wsRef.current || wsRef.current.readyState !== WebSocket.OPEN) {
      toast.error(t("wsDisconnected"));
      return;
    }
    sendApprovalDecision(wsRef.current, approvalId, "approve");
    setTurns((prev) =>
      prev.map((turn) =>
        turn.role === "approval" && turn.approvalId === approvalId
          ? { ...turn, approvalStatus: "approved" }
          : turn,
      ),
    );
  };

  const onMarkDecided = (approvalId: string, action: "approve" | "deny") => {
    setTurns((prev) =>
      prev.map((turn) =>
        turn.role === "approval" && turn.approvalId === approvalId
          ? {
              ...turn,
              approvalStatus: action === "approve" ? "approved" : "denied",
            }
          : turn,
      ),
    );
  };

  return (
    <div className="mx-auto flex h-full min-h-0 w-full max-w-4xl flex-1 flex-col overflow-hidden">
      <ChatHeader sessionId={sessionId} />
      <div className="min-h-0 flex-1 overflow-y-auto px-4 py-6">
        {turns.length === 0 && (
          <p className="text-center text-sm sh-muted">—</p>
        )}
        <MessageList
          turns={turns}
          sessionId={sessionId}
          canDecideApproval={canDecide}
          onApprove={onApproveQuick}
          onMarkDecided={onMarkDecided}
        />
        <div ref={scrollEndRef} />
      </div>

      <div className="flex shrink-0 items-center justify-between border-t px-4 py-1 text-[11px] sh-muted">
        <span>
          {usage
            ? `in: ${usage.input ?? 0} · out: ${usage.output ?? 0}`
            : ""}
        </span>
        <div className="flex items-center gap-2">
          <ShareDialog sessionId={sessionId} />
          <Link
            href={`/traces/${sessionId}`}
            className="inline-flex items-center gap-1 hover:text-[rgb(var(--color-primary))] hover:underline"
          >
            <IconActivity className="size-3" />
            {t("openTrace")}
          </Link>
        </div>
      </div>

      <div className="shrink-0">
        <ChatInput
          ref={inputRef}
          sessionId={sessionId}
          isStreaming={streaming}
          isConnected={wsConnected}
          onSend={send}
          onCancel={cancel}
        />
      </div>
    </div>
  );
}
