"use client";

/**
 * Adapted from Vercel AI SDK AI Elements (`InlineCitation` primitive).
 *
 * Inline citation badge — a small "host +N" pill that hovers / clicks open
 * a popover with the source titles, snippets, and URLs. Used by the
 * harness to attach grounding evidence to a specific phrase in the
 * assistant's reply.
 *
 * Built on the existing ``Popover`` primitive (shipped in
 * ``components/ui/popover.tsx``) so we reuse the same dismissal /
 * positioning behaviour everywhere.
 */

import { IconExternalLink } from "@tabler/icons-react";
import {
  type ComponentProps,
  type ReactNode,
} from "react";

import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from "@/components/ui/popover";
import { cn } from "@/lib/utils";

export type InlineCitationProps = ComponentProps<"span">;

export function InlineCitation({
  className,
  ...props
}: InlineCitationProps) {
  return (
    <span
      className={cn(
        "group inline-flex items-baseline gap-0.5 align-baseline",
        className,
      )}
      {...props}
    />
  );
}

export type InlineCitationTextProps = ComponentProps<"span">;

export function InlineCitationText({
  className,
  ...props
}: InlineCitationTextProps) {
  return (
    <span
      className={cn(
        "transition-colors group-hover:bg-[rgb(var(--color-primary))]/10 rounded-sm",
        className,
      )}
      {...props}
    />
  );
}

export interface InlineCitationCardProps {
  /** Optional sources used to compute the trigger label when ``children`` is
   *  not supplied. */
  sources?: string[];
  /** Trigger override (defaults to a "host +N" badge derived from sources). */
  trigger?: ReactNode;
  children: ReactNode;
  className?: string;
}

/**
 * Self-contained inline citation card. Encapsulates the
 * Popover plumbing + a tasteful trigger badge so callers don't have to wire
 * three subcomponents for the common case.
 */
export function InlineCitationCard({
  sources,
  trigger,
  children,
  className,
}: InlineCitationCardProps) {
  const host = (() => {
    if (!sources?.length) return null;
    try {
      return new URL(sources[0]!).hostname.replace(/^www\./, "");
    } catch {
      return null;
    }
  })();
  const moreCount = sources && sources.length > 1 ? sources.length - 1 : 0;
  return (
    <Popover>
      <PopoverTrigger asChild>
        <button
          type="button"
          aria-label="Show citation sources"
          className={cn(
            "ml-0.5 inline-flex items-center gap-0.5 rounded-full border bg-[rgb(var(--color-card))] px-1.5 py-0 text-[10px] font-medium leading-4 text-[rgb(var(--color-primary))] hover:bg-[rgb(var(--color-primary))]/10",
            className,
          )}
        >
          {trigger ?? (
            <>
              {host ?? "source"}
              {moreCount > 0 ? ` +${moreCount}` : null}
            </>
          )}
        </button>
      </PopoverTrigger>
      <PopoverContent className="w-80 p-0" align="start">
        {children}
      </PopoverContent>
    </Popover>
  );
}

export interface InlineCitationSourceProps extends ComponentProps<"div"> {
  title?: string;
  url?: string;
  description?: string;
}

export function InlineCitationSource({
  title,
  url,
  description,
  className,
  children,
  ...props
}: InlineCitationSourceProps) {
  return (
    <div className={cn("space-y-1 p-3", className)} {...props}>
      {title ? (
        <h4 className="truncate text-sm font-medium leading-tight">
          {url ? (
            <a
              href={url}
              target="_blank"
              rel="noreferrer"
              className="text-[rgb(var(--color-primary))] hover:underline"
            >
              <span className="inline-flex items-center gap-1">
                {title}
                <IconExternalLink className="size-3 shrink-0" />
              </span>
            </a>
          ) : (
            title
          )}
        </h4>
      ) : null}
      {url ? (
        <p className="truncate break-all text-[10px] sh-muted">{url}</p>
      ) : null}
      {description ? (
        <p className="line-clamp-3 text-xs leading-relaxed sh-muted">
          {description}
        </p>
      ) : null}
      {children}
    </div>
  );
}

export type InlineCitationQuoteProps = ComponentProps<"blockquote">;

export function InlineCitationQuote({
  className,
  ...props
}: InlineCitationQuoteProps) {
  return (
    <blockquote
      className={cn(
        "border-l-2 pl-3 text-sm italic sh-muted",
        className,
      )}
      {...props}
    />
  );
}
