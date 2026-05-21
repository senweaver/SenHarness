"use client";

/**
 * Adapted from Vercel AI SDK AI Elements (`Suggestion` primitive).
 *
 * Renders a horizontally scrollable strip of "follow-up" chips below the
 * last assistant message. Clicking a chip calls back with the chosen text
 * so the parent can shove it into the composer (or auto-send it).
 *
 * The data is supplied by the parent (typically via the
 * ``POST /api/v1/sessions/{id}/suggestions`` endpoint or a TanStack Query
 * hook); this component is purely presentational.
 */

import { IconArrowUpRight } from "@tabler/icons-react";
import type { ReactNode } from "react";

import { cn } from "@/lib/utils";

interface SuggestionProps {
  children: ReactNode;
  onClick?: () => void;
  className?: string;
  disabled?: boolean;
}

/** Single chip. Stays compact even when its label wraps. */
export function Suggestion({
  children,
  onClick,
  className,
  disabled = false,
}: SuggestionProps) {
  return (
    <button
      type="button"
      disabled={disabled}
      onClick={onClick}
      className={cn(
        "inline-flex shrink-0 items-center gap-1 rounded-full border bg-[rgb(var(--color-card))]/70 px-3 py-1 text-[11px] sh-muted transition-colors",
        "hover:border-[rgb(var(--color-primary))]/50 hover:text-[rgb(var(--color-primary))]",
        "disabled:cursor-not-allowed disabled:opacity-50",
        className,
      )}
      data-testid="suggestion-chip"
    >
      <span className="line-clamp-1 text-left">{children}</span>
      <IconArrowUpRight className="size-3 shrink-0 sh-muted" />
    </button>
  );
}

interface SuggestionsProps {
  children: ReactNode;
  className?: string;
}

/** Scrollable container — the chips wrap on narrow viewports. */
export function Suggestions({ children, className }: SuggestionsProps) {
  return (
    <div
      className={cn(
        "flex flex-wrap gap-1.5 overflow-x-auto pb-1",
        className,
      )}
      data-testid="suggestions"
    >
      {children}
    </div>
  );
}
