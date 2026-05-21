"use client";

/**
 * Adapted from Vercel AI SDK AI Elements (`Reasoning` primitive).
 *
 * Collapsible "thinking" section. Use it to render the contents of every
 * ``reasoning`` part of a UIMessage. While streaming we keep it open so the
 * user can watch the chain-of-thought; when streaming completes we render
 * a "Thought for N s" summary and the section auto-collapses.
 */

import { IconChevronDown, IconBrain } from "@tabler/icons-react";
import { useEffect, useRef, useState, type ReactNode } from "react";

import { cn } from "@/lib/utils";

interface ReasoningProps {
  children: ReactNode;
  /** True while reasoning chunks are still arriving for this part. */
  streaming?: boolean;
  /** Optional fixed duration label (used for hydrated history). */
  durationMs?: number;
  className?: string;
  /** Localised "Thinking…" / "Thought for {n}s" labels. */
  labels?: {
    streaming?: string;
    finished?: (seconds: number) => string;
  };
}

export function Reasoning({
  children,
  streaming = false,
  durationMs,
  className,
  labels,
}: ReasoningProps) {
  // Track when we started so we can show "Thought for Xs" once finished.
  const startedAtRef = useRef<number | null>(null);
  const [renderedDuration, setRenderedDuration] = useState<number | null>(null);
  const [open, setOpen] = useState(streaming);

  useEffect(() => {
    if (streaming && startedAtRef.current === null) {
      startedAtRef.current = performance.now();
    }
    if (!streaming && startedAtRef.current !== null) {
      const elapsed = performance.now() - startedAtRef.current;
      setRenderedDuration(elapsed);
      startedAtRef.current = null;
      // Auto-collapse a moment after streaming ends so users can scan the
      // result without manual interaction.
      const t = setTimeout(() => setOpen(false), 600);
      return () => clearTimeout(t);
    }
  }, [streaming]);

  const seconds = Math.max(
    1,
    Math.round(((durationMs ?? renderedDuration) ?? 0) / 1000),
  );
  const summary = streaming
    ? (labels?.streaming ?? "Thinking…")
    : (labels?.finished ?? ((s: number) => `Thought for ${s}s`))(seconds);

  return (
    <div
      className={cn(
        "rounded-lg border border-dashed bg-transparent px-3 py-1.5",
        className,
      )}
      data-testid="reasoning"
    >
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="flex w-full items-center gap-1.5 text-xs sh-muted"
        aria-expanded={open}
      >
        <IconBrain className="size-3" />
        <span>{summary}</span>
        <IconChevronDown
          className={cn(
            "ml-auto size-3 transition-transform",
            open && "rotate-180",
          )}
        />
      </button>
      {open ? (
        <div className="mt-1.5 max-h-72 overflow-y-auto text-[11px] sh-muted whitespace-pre-wrap font-mono">
          {children}
        </div>
      ) : null}
    </div>
  );
}
