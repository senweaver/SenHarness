"use client";

import { IconBrain, IconRobot, IconUser } from "@tabler/icons-react";
import { cn } from "@/lib/utils";
import { CopyButton } from "./CopyButton";
import { MarkdownContent } from "./MarkdownContent";
import { AttachmentView, type AttachmentRef } from "./AttachmentView";

export type MessageRole =
  | "user"
  | "assistant"
  | "thinking";

interface MessageItemProps {
  role: MessageRole;
  text?: string;
  attachments?: AttachmentRef[];
  /** True if assistant text is still streaming. Renders pulse cursor + suppresses copy/timestamp. */
  streaming?: boolean;
  /** ISO timestamp; rendered as HH:MM under the bubble. */
  timestamp?: string | null;
  /** Authoring user/agent display name shown under the avatar. */
  authorName?: string | null;
  /** Extra inline actions (e.g. RatingButtons) appended to the hover toolbar. */
  extras?: React.ReactNode;
  className?: string;
}

/**
 * `MessageItem` — a single user/assistant/thinking turn.
 *
 * Rendering rules:
 *   - **User**: right-aligned, mono background, plain text via `whitespace-pre-wrap`
 *     (markdown in user input is *not* rendered — typing `**bold**` should
 *     show literally so people can quote markdown without surprises).
 *   - **Assistant**: left-aligned, neutral card background, content goes
 *     through `MarkdownContent` (sanitised + GFM + code highlight). Streaming
 *     state appends a pulse cursor; an empty + streaming bubble shows the
 *     three-dot "thinking" indicator.
 *   - **Thinking**: collapsed `<details>` strip with the model's chain-of-
 *     thought (only emitted by some backends).
 */
export function MessageItem({
  role,
  text,
  attachments,
  streaming,
  timestamp,
  authorName,
  extras,
  className,
}: MessageItemProps) {
  if (role === "thinking") {
    return (
      <details
        className={cn(
          "ml-10 rounded-md border border-dashed bg-transparent px-3 py-1.5 text-xs sh-muted",
          className,
        )}
        data-testid="thinking-card"
      >
        <summary className="flex cursor-pointer items-center gap-1.5">
          <IconBrain className="size-3" />
          thinking
        </summary>
        <pre className="mt-1.5 whitespace-pre-wrap text-[11px] font-mono">
          {text}
        </pre>
      </details>
    );
  }

  const isUser = role === "user";
  const isThinkingPlaceholder = !isUser && streaming && !text;

  return (
    <div
      className={cn(
        "group flex gap-2 sm:gap-3 py-2",
        isUser && "flex-row-reverse",
        className,
      )}
      data-testid={isUser ? "user-message" : "assistant-message"}
      data-streaming={streaming ? "true" : "false"}
    >
      <div
        className={cn(
          "flex size-7 shrink-0 items-center justify-center rounded-full overflow-hidden",
          isUser
            ? "sh-primary"
            : "bg-[rgb(var(--color-primary))]/10 text-[rgb(var(--color-primary))]",
        )}
        title={authorName ?? undefined}
      >
        {isUser ? <IconUser className="size-3.5" /> : <IconRobot className="size-3.5" />}
      </div>

      <div
        className={cn(
          "flex min-w-0 flex-1 flex-col gap-1",
          isUser ? "items-end" : "items-start",
        )}
      >
        {/* attachments above the bubble for user msgs (matches typical chat UX) */}
        {isUser && attachments && attachments.length > 0 && (
          <div className="flex flex-wrap justify-end gap-1.5">
            {attachments.map((a) => (
              <AttachmentView key={a.id} att={a} />
            ))}
          </div>
        )}

        {/* the bubble */}
        {(text || isThinkingPlaceholder) && (
          <div
            className={cn(
              "max-w-[88%] sm:max-w-[85%] rounded-2xl px-3 py-2 text-sm break-words",
              isUser
                ? "sh-primary rounded-tr-sm"
                : "border sh-card rounded-tl-sm",
            )}
          >
            {isThinkingPlaceholder ? (
              <ThinkingDots />
            ) : isUser ? (
              <p className="whitespace-pre-wrap">{text}</p>
            ) : (
              <>
                <MarkdownContent content={text ?? ""} />
                {streaming && (
                  <span
                    aria-hidden="true"
                    className="ml-0.5 inline-block h-3.5 w-1 align-middle bg-current animate-pulse rounded-sm"
                  />
                )}
              </>
            )}
          </div>
        )}

        {/* assistant attachments below the bubble */}
        {!isUser && attachments && attachments.length > 0 && (
          <div className="flex flex-wrap gap-1.5">
            {attachments.map((a) => (
              <AttachmentView key={a.id} att={a} />
            ))}
          </div>
        )}

        {/* timestamp + copy + extras row (settled msgs only). The rating
            buttons live in `extras` so MessageItem stays decoupled from
            the ratings hook (parent injects them per role). Following the
            DeepSeek pattern: timestamp + copy are always visible; only
            the destructive icons hide until hover. */}
        {!streaming && text && (
          <div
            className={cn(
              "flex items-center gap-1.5 text-[11px] sh-muted",
              isUser && "flex-row-reverse",
            )}
          >
            {timestamp && (
              <span
                className="tabular-nums"
                title={new Date(timestamp).toLocaleString()}
              >
                {new Date(timestamp).toLocaleTimeString([], {
                  hour: "2-digit",
                  minute: "2-digit",
                })}
              </span>
            )}
            <CopyButton text={text} />
            {extras}
          </div>
        )}
      </div>
    </div>
  );
}

function ThinkingDots() {
  return (
    <div
      className="flex items-center gap-1.5"
      role="status"
      aria-live="polite"
      data-testid="thinking-dots"
    >
      <div className="flex gap-1" aria-hidden="true">
        <span className="size-1.5 rounded-full bg-[rgb(var(--color-muted))]/50 animate-bounce [animation-delay:0ms]" />
        <span className="size-1.5 rounded-full bg-[rgb(var(--color-muted))]/50 animate-bounce [animation-delay:150ms]" />
        <span className="size-1.5 rounded-full bg-[rgb(var(--color-muted))]/50 animate-bounce [animation-delay:300ms]" />
      </div>
      <span className="text-[11px] sh-muted">thinking…</span>
    </div>
  );
}
