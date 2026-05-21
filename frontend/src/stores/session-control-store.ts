"use client";

import { create } from "zustand";

import type { SessionEvent } from "@/lib/ws";

type ApprovalRequest = Extract<SessionEvent, { type: "approval_request" }>["data"];

interface ApprovalEntry extends ApprovalRequest {
  /** Status mirror that we can flip locally on approval_update frames. */
  status: "pending" | "approved" | "denied" | "expired" | "cancelled";
  reason?: string | null;
}

/** Four-state WebSocket lifecycle. The chat session page derives this
 * from preflight + transport open/close/error callbacks; consumers
 * (workspace pane status dot, ChatInput disabled state, etc.) read it
 * directly without touching the raw socket.
 *
 * ``error`` is distinct from ``closed``: the chat WS is short-lived by
 * design (one turn = one socket), so ``closed`` is the *idle* steady
 * state. ``error`` only fires when preflight fails or the socket dies
 * unexpectedly mid-stream — it's a "click to retry" surface. */
export type WsPhase = "connecting" | "open" | "closed" | "error";

interface PerSessionState {
  /** Three-state WS lifecycle. ``open`` ⇔ the legacy ``isOnline=true``. */
  wsPhase: WsPhase;
  /** Most recently observed run id, set by the transport. */
  runId: string | null;
  /** Local (per-tab) approval list — cancelled / approved on update frames. */
  approvals: ApprovalEntry[];
}

interface SessionControlState {
  bySession: Record<string, PerSessionState>;
  setWsPhase: (sessionId: string, phase: WsPhase) => void;
  /** Legacy boolean setter — kept so existing callers (the chat page
   *  control hook) compile unchanged. ``true`` maps to ``open``,
   *  ``false`` to ``closed``. New code should call ``setWsPhase``. */
  setOnline: (sessionId: string, online: boolean) => void;
  setRunId: (sessionId: string, runId: string | null) => void;
  upsertApproval: (sessionId: string, request: ApprovalRequest) => void;
  updateApproval: (
    sessionId: string,
    approvalId: string,
    patch: Partial<Pick<ApprovalEntry, "status" | "reason">>,
  ) => void;
  clearSession: (sessionId: string) => void;
}

const empty: PerSessionState = {
  wsPhase: "closed",
  runId: null,
  approvals: [],
};

/**
 * Per-session control-plane state. Lives next to the chat data plane
 * (``useChat({ transport })``); both share one WebSocket — the transport
 * forwards non-chat frames here via ``onControlEvent``.
 *
 * Why per-session: a user can keep multiple chat tabs open. Each tab's
 * approvals + runId are isolated so the wrong tab can't accidentally cancel
 * another tab's run.
 */
export const useSessionControlStore = create<SessionControlState>()((set) => ({
  bySession: {},
  setWsPhase: (sessionId, phase) =>
    set((state) => ({
      bySession: {
        ...state.bySession,
        [sessionId]: {
          ...(state.bySession[sessionId] ?? empty),
          wsPhase: phase,
        },
      },
    })),
  setOnline: (sessionId, online) =>
    set((state) => ({
      bySession: {
        ...state.bySession,
        [sessionId]: {
          ...(state.bySession[sessionId] ?? empty),
          // Legacy boolean → tri-state. We can't distinguish ``connecting``
          // from this entrypoint, so only flip between ``open`` and
          // ``closed``. Callers that want the connecting state must use
          // ``setWsPhase`` directly.
          wsPhase: online ? "open" : "closed",
        },
      },
    })),
  setRunId: (sessionId, runId) =>
    set((state) => ({
      bySession: {
        ...state.bySession,
        [sessionId]: {
          ...(state.bySession[sessionId] ?? empty),
          runId,
        },
      },
    })),
  upsertApproval: (sessionId, request) =>
    set((state) => {
      const cur = state.bySession[sessionId] ?? empty;
      const others = cur.approvals.filter((a) => a.id !== request.id);
      const next: ApprovalEntry = {
        ...request,
        status: "pending",
      };
      return {
        bySession: {
          ...state.bySession,
          [sessionId]: { ...cur, approvals: [...others, next] },
        },
      };
    }),
  updateApproval: (sessionId, approvalId, patch) =>
    set((state) => {
      const cur = state.bySession[sessionId] ?? empty;
      return {
        bySession: {
          ...state.bySession,
          [sessionId]: {
            ...cur,
            approvals: cur.approvals.map((a) =>
              a.id === approvalId ? { ...a, ...patch } : a,
            ),
          },
        },
      };
    }),
  clearSession: (sessionId) =>
    set((state) => {
      if (!(sessionId in state.bySession)) return state;
      const next = { ...state.bySession };
      delete next[sessionId];
      return { bySession: next };
    }),
}));

export type { ApprovalEntry };
