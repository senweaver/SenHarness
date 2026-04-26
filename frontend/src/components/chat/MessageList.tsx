"use client";

import { ApprovalCard, type ApprovalStatus } from "./ApprovalCard";
import { MessageItem } from "./MessageItem";
import { RatingButtons } from "./RatingButtons";
import { ToolCallCard, type ToolStatus } from "./ToolCallCard";
import type { AttachmentRef } from "./AttachmentView";
import { useSessionRatings } from "@/hooks/use-message-rating";
import type { MessageRatingSummary } from "@/types/api";

export type TurnRole =
  | "user"
  | "assistant"
  | "tool_call"
  | "thinking"
  | "approval";

export interface Turn {
  id: string;
  role: TurnRole;
  text?: string;
  attachments?: AttachmentRef[];
  streaming?: boolean;
  timestamp?: string | null;
  // tool_call
  toolName?: string;
  toolArgs?: Record<string, unknown>;
  toolResult?: unknown;
  toolStatus?: ToolStatus;
  // approval
  approvalId?: string;
  approvalSummary?: string | null;
  approvalExpiresAt?: string;
  approvalStatus?: ApprovalStatus;
}

interface MessageListProps {
  turns: Turn[];
  /** Session id — needed by the rating buttons we inject per assistant turn. */
  sessionId: string;
  /** Permission flag: only users with `decide_approval` see approve/deny buttons. */
  canDecideApproval: boolean;
  /** Quick approve via WS — fires when user clicks "Approve" on a card. */
  onApprove: (approvalId: string) => void;
  /** Optimistic local mark after a REST deny dialog completes. */
  onMarkDecided: (approvalId: string, action: "approve" | "deny") => void;
}

const UUID_RE =
  /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

/**
 * `MessageList` — turn dispatcher.
 *
 * Walks the timeline and routes each turn to the correct presenter component:
 *   - user / assistant / thinking → MessageItem
 *   - tool_call (any status)      → ToolCallCard
 *   - approval                    → ApprovalCard
 *
 * Tool cards render with a left indent so they visually nest under the
 * preceding assistant turn.
 */
export function MessageList({
  turns,
  sessionId,
  canDecideApproval,
  onApprove,
  onMarkDecided,
}: MessageListProps) {
  // Pre-fetch the rating summary for the whole session so each assistant
  // bubble can render its like / dislike counts in one round-trip.
  const ratingsQ = useSessionRatings(sessionId || null);
  const ratingMap = new Map<string, MessageRatingSummary>();
  for (const r of ratingsQ.data ?? []) {
    ratingMap.set(r.message_id, r);
  }

  return (
    <div className="space-y-1">
      {turns.map((turn) => {
        if (turn.role === "tool_call") {
          return (
            <div key={turn.id} className="ml-9 mr-2">
              <ToolCallCard
                toolCall={{
                  id: turn.id,
                  name: turn.toolName ?? "tool",
                  args: turn.toolArgs ?? {},
                  result: turn.toolResult,
                  status:
                    turn.toolStatus ??
                    (turn.toolResult !== undefined ? "completed" : "pending"),
                }}
              />
            </div>
          );
        }
        if (turn.role === "approval" && turn.approvalId) {
          return (
            <div key={turn.id} className="ml-9 mr-2">
              <ApprovalCard
                approvalId={turn.approvalId}
                toolName={turn.toolName ?? "tool"}
                toolArgs={turn.toolArgs ?? {}}
                summary={turn.approvalSummary ?? null}
                expiresAt={turn.approvalExpiresAt}
                status={turn.approvalStatus ?? "pending"}
                canDecide={canDecideApproval}
                onApprove={onApprove}
                onLocalUpdate={onMarkDecided}
              />
            </div>
          );
        }
        const isAssistant = turn.role === "assistant";
        // Only attach RatingButtons when the turn id is a real server-issued
        // UUID (set on hydrated history or after the FINAL frame). Streaming
        // bubbles use a placeholder UUID until FINAL replaces it; rating an
        // unknown id would 404 the server.
        const showRating =
          isAssistant && !turn.streaming && UUID_RE.test(turn.id);
        return (
          <MessageItem
            key={turn.id}
            role={turn.role as "user" | "assistant" | "thinking"}
            text={turn.text}
            attachments={turn.attachments}
            streaming={turn.streaming}
            timestamp={turn.timestamp}
            extras={
              showRating ? (
                <RatingButtons
                  sessionId={sessionId}
                  messageId={turn.id}
                  summary={ratingMap.get(turn.id)}
                />
              ) : null
            }
          />
        );
      })}
    </div>
  );
}
