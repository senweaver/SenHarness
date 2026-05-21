"use client";

/**
 * Adapted from Vercel AI SDK AI Elements (`Sources` primitive).
 *
 * Compact, collapsible "Used N sources" disclosure rendered alongside an
 * assistant message that cited external context (web search, knowledge
 * library, shared docs, …).
 *
 * Why not Radix Collapsible? We don't ship that dependency; a `<details>`
 * element gives us the same accessible disclosure semantics for free,
 * keeps the bundle small, and survives SSR without hydration drama.
 *
 * Composition:
 *
 *     <Sources>
 *       <SourcesTrigger count={results.length} />
 *       <SourcesContent>
 *         {results.map(r => (
 *           <Source key={r.id} href={r.url} title={r.title} />
 *         ))}
 *       </SourcesContent>
 *     </Sources>
 */

import { IconBook2, IconChevronDown, IconExternalLink } from "@tabler/icons-react";
import type { ComponentProps, ReactNode } from "react";

import { cn } from "@/lib/utils";

export type SourcesProps = ComponentProps<"details">;

export function Sources({ className, children, ...props }: SourcesProps) {
  return (
    <details
      className={cn(
        "group not-prose mb-2 rounded-md border bg-[rgb(var(--color-card))]/50 px-2 py-1 text-xs",
        className,
      )}
      {...props}
    >
      {children}
    </details>
  );
}

export type SourcesTriggerProps = ComponentProps<"summary"> & {
  /** Number of source rows in the collapsed body. */
  count: number;
  /** Override the default "Used N sources" label. */
  label?: ReactNode;
};

export function SourcesTrigger({
  className,
  count,
  label,
  children,
  ...props
}: SourcesTriggerProps) {
  return (
    <summary
      className={cn(
        "flex cursor-pointer list-none items-center gap-2 select-none",
        "[&::-webkit-details-marker]:hidden",
        className,
      )}
      {...props}
    >
      {children ?? (
        <>
          <IconBook2 className="size-3.5 text-[rgb(var(--color-primary))]" />
          <span className="font-medium">{label ?? `Used ${count} sources`}</span>
          <IconChevronDown className="ml-auto size-3 sh-muted transition-transform group-open:rotate-180" />
        </>
      )}
    </summary>
  );
}

export type SourcesContentProps = ComponentProps<"div">;

export function SourcesContent({
  className,
  ...props
}: SourcesContentProps) {
  return (
    <div
      className={cn("mt-2 flex flex-col gap-1.5 pl-1", className)}
      {...props}
    />
  );
}

export interface SourceProps extends ComponentProps<"a"> {
  /** Optional supporting text rendered under the link. */
  description?: string;
  title?: string;
}

export function Source({
  href,
  title,
  description,
  className,
  children,
  ...props
}: SourceProps) {
  const host = (() => {
    if (!href) return null;
    try {
      return new URL(href).hostname;
    } catch {
      return null;
    }
  })();
  return (
    <a
      className={cn(
        "flex flex-col gap-0.5 rounded-md px-1.5 py-1 transition-colors hover:bg-black/5 dark:hover:bg-white/5",
        className,
      )}
      href={href}
      rel="noreferrer"
      target="_blank"
      {...props}
    >
      {children ?? (
        <>
          <span className="flex items-center gap-1.5 truncate font-medium text-[rgb(var(--color-primary))]">
            <span className="truncate">{title ?? href ?? "untitled"}</span>
            <IconExternalLink className="size-3 shrink-0" />
          </span>
          {host ? (
            <span className="truncate text-[10px] sh-muted">{host}</span>
          ) : null}
          {description ? (
            <span className="line-clamp-2 text-[11px] sh-muted">
              {description}
            </span>
          ) : null}
        </>
      )}
    </a>
  );
}
