"use client";

/**
 * Adapted from Vercel AI SDK AI Elements (`PromptInput` primitive set).
 *
 * Composer building blocks: outer form / textarea / toolbar / submit.
 * Component composition mirrors the upstream API:
 *
 *   <PromptInput onSubmit={...}>
 *     <PromptInputTextarea />
 *     <PromptInputToolbar>
 *       <PromptInputTools>...</PromptInputTools>
 *       <PromptInputSubmit status={...} />
 *     </PromptInputToolbar>
 *   </PromptInput>
 */

import {
  IconLoader2,
  IconPlayerStop,
  IconSend,
} from "@tabler/icons-react";
import {
  forwardRef,
  type ComponentPropsWithoutRef,
  type FormEvent,
  type ReactNode,
} from "react";

import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { cn } from "@/lib/utils";

// ─── Outer form ────────────────────────────────────────────
interface PromptInputProps
  extends Omit<ComponentPropsWithoutRef<"form">, "onSubmit"> {
  onSubmit: (e: FormEvent<HTMLFormElement>) => void;
  children: ReactNode;
}

export const PromptInput = forwardRef<HTMLFormElement, PromptInputProps>(
  function PromptInput({ className, children, onSubmit, ...props }, ref) {
    return (
      <form
        ref={ref}
        // Use the native ``onSubmit`` event so PromptInputTextarea's
        // requestSubmit() (fired on Enter) works without a JS-only path.
        onSubmit={(e) => {
          e.preventDefault();
          onSubmit(e);
        }}
        className={cn(
          "flex flex-col gap-2 rounded-2xl border bg-[rgb(var(--color-card))]/60 p-2 shadow-sm focus-within:border-[rgb(var(--color-primary))]",
          className,
        )}
        {...props}
      >
        {children}
      </form>
    );
  },
);

// ─── Textarea ─────────────────────────────────────────────
/**
 * Tailwind classes shared by the composer textarea **and** any overlay
 * mirror (e.g. ``ComposerOverlay`` for inline ``/skill`` and ``@mention``
 * highlighting). Padding, font-size, line-height, font-family and
 * word-break must match exactly so the overlay's highlight spans land
 * on the same glyphs the textarea is painting. Edit only here — both
 * surfaces import this constant.
 */
export const COMPOSER_TEXT_CLASS =
  "min-h-[36px] max-h-[200px] resize-none border-0 bg-transparent p-2 text-sm leading-6 whitespace-pre-wrap break-words";

interface PromptInputTextareaProps
  extends ComponentPropsWithoutRef<typeof Textarea> {
  /** Min row count; defaults to 1. */
  rows?: number;
  /** Max pixel height before the textarea becomes scrollable. */
  maxHeight?: number;
}

export const PromptInputTextarea = forwardRef<
  HTMLTextAreaElement,
  PromptInputTextareaProps
>(function PromptInputTextarea(
  { className, onKeyDown, rows = 1, maxHeight = 200, ...props },
  ref,
) {
  const handleKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    // IME-friendly: skip Enter while a composition (CJK / emoji picker) is
    // active. ``isComposing`` is the standards-compliant signal.
    if (
      e.key === "Enter" &&
      !e.shiftKey &&
      !e.ctrlKey &&
      !e.metaKey &&
      !e.nativeEvent.isComposing
    ) {
      e.preventDefault();
      e.currentTarget.form?.requestSubmit();
    }
    onKeyDown?.(e);
  };

  return (
    <Textarea
      ref={ref}
      rows={rows}
      onKeyDown={handleKeyDown}
      onInput={(e) => {
        const el = e.currentTarget;
        el.style.height = "auto";
        el.style.height = `${Math.min(el.scrollHeight, maxHeight)}px`;
      }}
      className={cn(
        COMPOSER_TEXT_CLASS,
        "shadow-none focus-visible:ring-0",
        className,
      )}
      data-testid="chat-input"
      {...props}
    />
  );
});

// ─── Toolbar / tools row ──────────────────────────────────
export function PromptInputToolbar({
  children,
  className,
  ...props
}: ComponentPropsWithoutRef<"div">) {
  return (
    <div
      className={cn(
        "flex items-center justify-between gap-2 px-1",
        className,
      )}
      {...props}
    >
      {children}
    </div>
  );
}

export function PromptInputTools({
  children,
  className,
  ...props
}: ComponentPropsWithoutRef<"div">) {
  return (
    <div
      className={cn("flex items-center gap-1", className)}
      {...props}
    >
      {children}
    </div>
  );
}

// ─── Submit / Stop button ─────────────────────────────────
export type PromptInputStatus =
  | "ready"
  | "submitted"
  | "streaming"
  | "error";

interface PromptInputSubmitProps
  extends ComponentPropsWithoutRef<typeof Button> {
  status: PromptInputStatus;
  /** Disable the send button (overrides default canSubmit logic). */
  disabled?: boolean;
}

export const PromptInputSubmit = forwardRef<
  HTMLButtonElement,
  PromptInputSubmitProps
>(function PromptInputSubmit(
  { status, disabled, className, onClick, ...props },
  ref,
) {
  const isStreaming = status === "streaming" || status === "submitted";
  return (
    <Button
      ref={ref}
      type={isStreaming ? "button" : "submit"}
      variant={isStreaming ? "destructive" : "default"}
      size="icon"
      disabled={!isStreaming && disabled}
      onClick={onClick}
      className={cn("size-8 shrink-0", className)}
      data-testid={isStreaming ? "chat-cancel" : "chat-send"}
      aria-label={isStreaming ? "Stop generating" : "Send message"}
      {...props}
    >
      {status === "submitted" ? (
        <IconLoader2 className="size-4 animate-spin" />
      ) : isStreaming ? (
        <IconPlayerStop className="size-4" />
      ) : (
        <IconSend className="size-4" />
      )}
    </Button>
  );
});
