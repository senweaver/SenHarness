"use client";

import { useCallback, useEffect, useMemo, useRef } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { useTranslations } from "next-intl";
import { toast } from "sonner";

import { refreshSilently } from "@/lib/api";
import type { ControlEvent } from "@/lib/chat-transport";
import { sendApprovalDecision, WS_CLOSE } from "@/lib/ws";
import {
  type ApprovalEntry,
  type WsPhase,
  useSessionControlStore,
} from "@/stores/session-control-store";
import { useWorkspacePaneStore } from "@/stores/workspace-pane-store";

/**
 * Module-level socket registry keyed by ``sessionId``.
 *
 * The chat session page owns the live ``WebSocket`` (it lives inside the
 * ``SenHarnessWsTransport``) and binds it via ``bindSocket(...)``. The
 * three-pane chat shell renders the ``WorkspacePanel`` *outside* the page
 * boundary, so its ``decideApproval`` callback can't reach the page-local
 * socket ref directly. Publishing the socket here lets any sibling
 * surface (workspace rail, approval card, etc.) call ``decideApprovalFor``
 * without re-implementing the bind dance.
 *
 * Only the most recent socket per session is kept; ``bindSocket(null)``
 * (or session unmount) removes the entry.
 */
const sessionSockets = new Map<string, WebSocket>();

/**
 * Standalone variant of ``decideApproval`` usable outside React. Shares the
 * registry above with every ``useSessionControl`` hook instance so the
 * caller doesn't need to thread the socket through props.
 */
export function decideApprovalFor(
  sessionId: string | null | undefined,
  approvalId: string,
  action: "approve" | "deny",
): boolean {
  if (!sessionId) return false;
  const ws = sessionSockets.get(sessionId);
  if (!ws || ws.readyState !== WebSocket.OPEN) return false;
  sendApprovalDecision(ws, approvalId, action);
  useSessionControlStore.getState().updateApproval(sessionId, approvalId, {
    status: action === "approve" ? "approved" : "denied",
  });
  return true;
}

/**
 * Read + react to a single chat session's control plane (approvals, online
 * state, AI title broadcasts). Designed to be called once per session route
 * and to share its WebSocket with ``useChat`` via the transport's
 * ``onControlEvent`` callback.
 *
 * This hook is the **only** business-side entry point for ``lib/ws.ts`` —
 * the chat session page binds the live socket via ``bindSocket(ws)`` and
 * the hook handles approval ack frames + 4401 auto-refresh internally.
 *
 * Returns:
 *   - ``approvals``       : pending approval requests for the active session
 *   - ``isOnline``        : true when the WS reports OPEN
 *   - ``runId``           : currently active run id (mirrors the transport)
 *   - ``onControlEvent``  : the bridge the transport calls back to. Pass it
 *                          straight into ``new SenHarnessWsTransport(...)``.
 *   - ``onRunIdAssigned`` : bridge for the transport to update the runId.
 *   - ``setIsOnline``     : bridge the chat-page can call when the WS opens.
 *   - ``bindSocket``      : pass the live ``WebSocket`` (or null on close);
 *                          the hook re-attaches a ``close`` listener that
 *                          refreshes the access token on ``WS_CLOSE.AUTH_EXPIRED``.
 *   - ``decideApproval``  : ack a pending approval through the bound socket
 *                          and flip the local store optimistically.
 */
export function useSessionControl(sessionId: string | null | undefined) {
  const qc = useQueryClient();
  const t = useTranslations("chat.systemFrame");
  const setWsPhaseStore = useSessionControlStore((s) => s.setWsPhase);
  const setOnline = useSessionControlStore((s) => s.setOnline);
  const setRunId = useSessionControlStore((s) => s.setRunId);
  const upsertApproval = useSessionControlStore((s) => s.upsertApproval);
  const updateApproval = useSessionControlStore((s) => s.updateApproval);
  const clearSession = useSessionControlStore((s) => s.clearSession);
  const openTab = useWorkspacePaneStore((s) => s.openTab);

  const state = useSessionControlStore((s) =>
    sessionId ? s.bySession[sessionId] : undefined,
  );

  // Live socket reference for the approval ack path. Kept in a ref so the
  // ``decideApproval`` callback identity stays stable across re-renders.
  const wsRef = useRef<WebSocket | null>(null);
  // Track which socket we last attached our auto-refresh listener to so the
  // ``bindSocket`` callback can detach cleanly when a new one replaces it.
  const closeListenerRef = useRef<{
    ws: WebSocket;
    handler: (ev: CloseEvent) => void;
  } | null>(null);

  // Tear down the per-session entry when the route unmounts so memory does
  // not grow unbounded across navigations. Also drop the close listener so
  // we don't leak across navigations.
  useEffect(() => {
    if (!sessionId) return;
    return () => {
      clearSession(sessionId);
      const prev = closeListenerRef.current;
      if (prev) {
        try {
          prev.ws.removeEventListener("close", prev.handler);
        } catch {
          /* socket already gone */
        }
        closeListenerRef.current = null;
      }
      // Clear the shared registry so cross-route consumers (e.g. the
      // layout-level workspace pane) don't keep firing into a dead socket.
      const cur = sessionSockets.get(sessionId);
      if (cur && cur === wsRef.current) sessionSockets.delete(sessionId);
      wsRef.current = null;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sessionId]);

  const onControlEvent = useMemo(
    () =>
      (event: ControlEvent): void => {
        if (!sessionId) return;
        switch (event.type) {
          case "approval_request": {
            upsertApproval(sessionId, event.data);
            // Auto-open the Approvals tab so the user notices.
            openTab("approvals", { approvalId: event.data.id });
            break;
          }
          case "approval_update": {
            const status = (event.data.status as ApprovalEntry["status"]) || "pending";
            updateApproval(sessionId, event.data.id, {
              status,
              reason: event.data.reason ?? null,
            });
            break;
          }
          case "session_title_updated": {
            // Refresh the cached session detail + listing so the new title
            // shows up in AgentSwitcher and SessionList without a full reload.
            qc.invalidateQueries({ queryKey: ["sessions", "detail"] });
            qc.invalidateQueries({ queryKey: ["sessions", "recent"] });
            break;
          }
          case "system": {
            const data = event.data as Record<string, unknown>;
            const kind = String(data.kind ?? "");
            if (kind === "goal") {
              const action = String(data.action ?? "");
              const ok = data.ok === true;
              if (action === "lock" && ok) {
                toast.success(
                  t("goalLocked", {
                    text: String(data.goal_text ?? ""),
                  }),
                );
              } else if (action === "unlock" && ok) {
                toast.success(t("goalUnlocked"));
              } else if (action === "unlock" && data.reason === "no_active_goal") {
                toast.message(t("goalUnlockNoActive"));
              } else if (action === "status") {
                const active = data.active as
                  | { goal_text?: string }
                  | null
                  | undefined;
                toast.success(
                  active
                    ? t("goalStatusActive", {
                        text: String(active.goal_text ?? ""),
                      })
                    : t("goalStatusNone"),
                );
              }
              qc.invalidateQueries({
                queryKey: ["sessions", "goals"],
                predicate: (q) =>
                  Array.isArray(q.queryKey) &&
                  (q.queryKey as unknown[])[0] === "sessions" &&
                  (q.queryKey as unknown[])[3] === sessionId,
              });
            } else if (kind === "insights_queued") {
              const days = Number(data.days ?? 0);
              toast.success(t("insightsQueued", { days }));
            }
            break;
          }
          case "resume_ack":
          case "pong":
          default:
            break;
        }
      },
    [sessionId, upsertApproval, updateApproval, openTab, qc, t],
  );

  const onRunIdAssigned = useMemo(
    () =>
      (runId: string | null): void => {
        if (!sessionId) return;
        setRunId(sessionId, runId);
      },
    [sessionId, setRunId],
  );

  const setIsOnline = useMemo(
    () =>
      (online: boolean): void => {
        if (!sessionId) return;
        setOnline(sessionId, online);
      },
    [sessionId, setOnline],
  );

  const setWsPhase = useMemo(
    () =>
      (phase: WsPhase): void => {
        if (!sessionId) return;
        setWsPhaseStore(sessionId, phase);
      },
    [sessionId, setWsPhaseStore],
  );

  /** Bind the live transport socket. Detaches any previous close listener
   *  and re-attaches a 4401-aware refresh on the new socket. Pass ``null``
   *  to detach without rebinding (e.g. when the transport tears down).
   *
   *  Also publishes the socket into the module-level ``sessionSockets``
   *  registry so cross-component consumers (the layout-level workspace
   *  pane in particular) can call ``decideApprovalFor`` without threading
   *  the WebSocket through React props. */
  const bindSocket = useCallback(
    (ws: WebSocket | null): void => {
      const prev = closeListenerRef.current;
      if (prev && prev.ws !== ws) {
        try {
          prev.ws.removeEventListener("close", prev.handler);
        } catch {
          /* ignore */
        }
        closeListenerRef.current = null;
      }
      wsRef.current = ws;
      if (sessionId) {
        if (ws) sessionSockets.set(sessionId, ws);
        else if (sessionSockets.get(sessionId) === prev?.ws)
          sessionSockets.delete(sessionId);
      }
      if (!ws || prev?.ws === ws) return;
      const handler = (e: CloseEvent) => {
        if (e.code === WS_CLOSE.AUTH_EXPIRED) {
          // Token expired mid-stream; the next sendMessage will rebuild the
          // socket. Refresh the access token in the background so that
          // re-handshake comes in with a fresh JWT.
          refreshSilently().catch(() => {
            /* refresh failure surfaces on the next API call */
          });
        }
      };
      ws.addEventListener("close", handler);
      closeListenerRef.current = { ws, handler };
    },
    [sessionId],
  );

  /** Ack a pending approval. Sends the WS frame + flips the local store
   *  optimistically so the card updates without waiting for the
   *  ``approval_update`` round trip. Falls back to the shared registry
   *  when the local ref is stale (e.g. caller is the workspace pane that
   *  lives outside the page tree that owns the transport). */
  const decideApproval = useCallback(
    (approvalId: string, action: "approve" | "deny"): boolean => {
      if (!sessionId) return false;
      const ws =
        wsRef.current && wsRef.current.readyState === WebSocket.OPEN
          ? wsRef.current
          : (sessionSockets.get(sessionId) ?? null);
      if (!ws || ws.readyState !== WebSocket.OPEN) return false;
      sendApprovalDecision(ws, approvalId, action);
      updateApproval(sessionId, approvalId, {
        status: action === "approve" ? "approved" : "denied",
      });
      return true;
    },
    [sessionId, updateApproval],
  );

  return {
    approvals: state?.approvals ?? [],
    /** Derived from ``wsPhase``. ``true`` only when fully open. */
    isOnline: state?.wsPhase === "open",
    /** Three-state lifecycle exposed to consumers that need to distinguish
     *  the "connecting" phase (e.g. amber pulse on the workspace pane). */
    wsPhase: state?.wsPhase ?? ("closed" as WsPhase),
    runId: state?.runId ?? null,
    onControlEvent,
    onRunIdAssigned,
    setIsOnline,
    setWsPhase,
    bindSocket,
    decideApproval,
  };
}
