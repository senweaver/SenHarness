"use client";

/**
 * Adapted from Vercel AI SDK AI Elements (`Message` primitive).
 *
 * Role-aware container for a single transcript turn. Visual rules:
 *
 *   - user      → right-aligned, primary tinted bubble.
 *   - assistant → left-aligned, neutral card bubble. Children are typically
 *                 a `<Response>` plus optional `<Tool>` / `<Reasoning>` parts.
 *   - system    → muted single-line strip (rare; warning-only).
 */

import { IconUser } from "@tabler/icons-react";
import type { ComponentPropsWithoutRef, ReactNode } from "react";

import { AgentAvatar } from "@/components/agents/AgentAvatar";
import { cn } from "@/lib/utils";

type MessageRole = "user" | "assistant" | "system" | "tool";

interface MessageProps extends ComponentPropsWithoutRef<"div"> {
  role: MessageRole;
  children: ReactNode;
  /** Authoring agent or user display name (rendered as avatar tooltip). */
  authorName?: string | null;
  /** Optional avatar image; falls back to a role-tinted icon. */
  avatarUrl?: string | null;
}

export function Message({
  role,
  children,
  authorName,
  avatarUrl,
  className,
  ...props
}: MessageProps) {
  if (role === "system" || role === "tool") {
    return (
      <div
        className={cn(
          "mx-auto my-1 max-w-prose rounded-md border-dashed bg-transparent px-3 py-1 text-[11px] sh-muted",
          className,
        )}
        {...props}
      >
        {children}
      </div>
    );
  }

  const isUser = role === "user";
  return (
    <div
      className={cn(
        "group flex gap-2 py-2 sm:gap-3",
        isUser && "flex-row-reverse",
        className,
      )}
      data-role={role}
      data-testid={isUser ? "user-message" : "assistant-message"}
      {...props}
    >
      <MessageAvatar
        role={role}
        authorName={authorName}
        avatarUrl={avatarUrl}
      />
      <MessageContent role={role}>{children}</MessageContent>
    </div>
  );
}

function MessageAvatar({
  role,
  authorName,
  avatarUrl,
}: {
  role: MessageRole;
  authorName?: string | null;
  avatarUrl?: string | null;
}) {
  if (role !== "user") {
    // Reuse the same agent avatar (image or themed initial) shown in the
    // chat header / switcher so the conversation thread stays consistent.
    return (
      <AgentAvatar
        name={authorName}
        avatarUrl={avatarUrl}
        className="size-7"
        fallbackClassName="text-[12px]"
      />
    );
  }
  if (avatarUrl) {
    return (
      // eslint-disable-next-line @next/next/no-img-element
      <img
        src={avatarUrl}
        alt={authorName ?? ""}
        className="size-7 shrink-0 rounded-full object-cover"
      />
    );
  }
  return (
    <div
      className="flex size-7 shrink-0 items-center justify-center overflow-hidden rounded-full sh-primary"
      title={authorName ?? undefined}
      aria-hidden="true"
    >
      <IconUser className="size-3.5" />
    </div>
  );
}

interface MessageContentProps {
  role: MessageRole;
  children: ReactNode;
  className?: string;
}

/**
 * Wraps the actual content of a Message. Children compose freely — a `<Response>`,
 * a `<Tool>` etc. We don't force every message into a bubble: assistant turns
 * with structural cards (tool, plan) sit flush so cards look natural.
 */
export function MessageContent({ role, children, className }: MessageContentProps) {
  const isUser = role === "user";
  return (
    <div
      className={cn(
        "flex min-w-0 flex-1 flex-col gap-1.5",
        isUser ? "items-end" : "items-start",
        className,
      )}
    >
      {children}
    </div>
  );
}
