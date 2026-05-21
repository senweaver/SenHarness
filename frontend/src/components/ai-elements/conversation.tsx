"use client";

/**
 * Adapted from Vercel AI SDK AI Elements (`Conversation` primitive).
 *
 * `Conversation` is the scrollable container that owns the auto-stick-to-bottom
 * behaviour every chat surface needs. It exposes a fixed-position "Jump to
 * latest" button that only shows up when the user has scrolled away from the
 * bottom.
 *
 * Usage:
 *   <Conversation>
 *     <ConversationContent>
 *       {messages.map(m => <Message key={m.id} role={m.role}>...</Message>)}
 *     </ConversationContent>
 *     <ConversationScrollButton />
 *   </Conversation>
 */

import { IconArrowDown } from "@tabler/icons-react";
import { type ComponentPropsWithoutRef, type ReactNode, type Ref } from "react";
import {
  StickToBottom,
  type StickToBottomContext,
  useStickToBottomContext,
} from "use-stick-to-bottom";

import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

interface ConversationProps extends ComponentPropsWithoutRef<"div"> {
  children: ReactNode;
  /**
   * Optional ref that receives the live ``StickToBottomContext``. Use it
   * to imperatively re-engage the bottom-lock after a user submit so the
   * streaming response remains in view even if the user had scrolled
   * away. ``isAtBottom = false`` disables the package's auto-follow on
   * resize, so callers MUST trigger ``scrollToBottom()`` themselves to
   * recover.
   */
  contextRef?: Ref<StickToBottomContext>;
}

/**
 * Top-level scroll surface. Uses ``flex-1 min-h-0`` so it fills the
 * remaining height of its flex-column parent (which holds the goal
 * banner, artifact strip, composer, etc.) instead of greedily
 * claiming 100% and overflowing the shell.
 */
export function Conversation({
  className,
  children,
  contextRef,
  ...props
}: ConversationProps) {
  return (
    <StickToBottom
      className={cn(
        "relative flex min-h-0 w-full flex-1 flex-col overflow-hidden",
        className,
      )}
      resize="smooth"
      initial="smooth"
      contextRef={contextRef}
      {...props}
    >
      {children}
    </StickToBottom>
  );
}

interface ConversationContentProps extends ComponentPropsWithoutRef<"div"> {
  children: ReactNode;
}

/**
 * Inner content wrapper. ``StickToBottom.Content`` keeps the viewport pinned
 * to the bottom while new messages stream in.
 */
export function ConversationContent({
  className,
  children,
  ...props
}: ConversationContentProps) {
  return (
    <StickToBottom.Content
      className={cn(
        "mx-auto w-full max-w-3xl flex-1 px-3 py-4 sm:px-6 sm:py-6",
        className,
      )}
      {...props}
    >
      {children}
    </StickToBottom.Content>
  );
}

interface ConversationScrollButtonProps {
  className?: string;
  /** Optional accessible label override; defaults to a generic English string. */
  ariaLabel?: string;
}

/**
 * Floating "Jump to latest" pill. Only renders when the user has scrolled
 * away from the bottom — `use-stick-to-bottom` exposes that flag via context.
 */
export function ConversationScrollButton({
  className,
  ariaLabel = "Scroll to latest message",
}: ConversationScrollButtonProps) {
  const { isAtBottom, scrollToBottom } = useStickToBottomContext();
  if (isAtBottom) return null;
  return (
    <div className="pointer-events-none absolute inset-x-0 bottom-3 flex justify-center">
      <Button
        type="button"
        size="sm"
        variant="subtle"
        aria-label={ariaLabel}
        title={ariaLabel}
        onClick={() => scrollToBottom()}
        className={cn(
          "pointer-events-auto rounded-full shadow-md",
          className,
        )}
      >
        <IconArrowDown className="size-4" />
      </Button>
    </div>
  );
}
