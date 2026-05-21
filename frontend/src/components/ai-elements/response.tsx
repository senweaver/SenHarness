"use client";

/**
 * Streaming-friendly Markdown renderer.
 *
 * Streamdown's default ``mode="streaming"`` wraps its parsed-block setState
 * in ``startTransition``, which React treats as low-priority and keeps
 * deferring whenever a higher-priority render (our rAF tick) arrives —
 * leaving the rendered markdown frozen until the stream ends.  Forcing
 * ``mode="static"`` skips the transition wrap and re-parses on every render,
 * which combined with ``parseIncompleteMarkdown`` while streaming gives a
 * live markdown render that grows token-by-token.
 */

import { Streamdown } from "streamdown";

import { cn } from "@/lib/utils";

interface ResponseProps {
  children: string | undefined | null;
  className?: string;
  streaming?: boolean;
  id?: string;
}

const PROSE_CLS =
  "prose-streamdown max-w-none break-words text-sm [&_a]:text-[rgb(var(--color-primary))] [&_a:hover]:underline";

const CURSOR = (
  <span
    aria-hidden="true"
    className="ml-0.5 inline-block h-3.5 w-1 animate-pulse rounded-sm bg-current align-middle"
  />
);

export function Response({
  children,
  className,
  streaming = false,
  id,
}: ResponseProps) {
  const text = (children ?? "").toString();

  return (
    <div
      data-streaming={streaming ? "true" : "false"}
      data-testid="response"
      className={cn(PROSE_CLS, className)}
    >
      <Streamdown
        mode="static"
        parseIncompleteMarkdown={streaming}
        key={id}
      >
        {text}
      </Streamdown>
      {streaming && CURSOR}
    </div>
  );
}
