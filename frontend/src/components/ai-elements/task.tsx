"use client";

/**
 * Adapted from Vercel AI SDK AI Elements (`Task` primitive).
 *
 * Collapsible "task" group used by the harness to disclose multi-step
 * work the agent did under one umbrella (e.g. "Searched 3 sources",
 * "Edited 2 files"). Children render inline; ``<TaskItemFile>`` is a
 * convenience chip for filename pills.
 *
 * Same ``<details>`` strategy as ``<Sources>`` — accessible disclosure
 * without a Radix dependency.
 */

import { IconChevronDown, IconSearch } from "@tabler/icons-react";
import type { ComponentProps, ReactNode } from "react";

import { cn } from "@/lib/utils";

export type TaskProps = ComponentProps<"details"> & {
  defaultOpen?: boolean;
};

export function Task({
  defaultOpen = true,
  className,
  open,
  children,
  ...props
}: TaskProps) {
  return (
    <details
      open={open ?? defaultOpen}
      className={cn(
        "group not-prose my-1 rounded-md border bg-[rgb(var(--color-card))]/50 px-2 py-1 text-xs",
        className,
      )}
      {...props}
    >
      {children}
    </details>
  );
}

export type TaskTriggerProps = ComponentProps<"summary"> & {
  /** Title shown on the disclosure row when no children are provided. */
  title?: ReactNode;
  /** Optional leading icon (defaults to a search glyph). */
  icon?: ReactNode;
};

export function TaskTrigger({
  className,
  title,
  icon,
  children,
  ...props
}: TaskTriggerProps) {
  return (
    <summary
      className={cn(
        "flex cursor-pointer list-none items-center gap-2 select-none",
        "[&::-webkit-details-marker]:hidden",
        "sh-muted hover:text-[rgb(var(--color-fg))]",
        className,
      )}
      {...props}
    >
      {children ?? (
        <>
          <span className="text-[rgb(var(--color-primary))]">
            {icon ?? <IconSearch className="size-3.5" />}
          </span>
          <span className="truncate font-medium">{title}</span>
          <IconChevronDown className="ml-auto size-3 transition-transform group-open:rotate-180" />
        </>
      )}
    </summary>
  );
}

export type TaskContentProps = ComponentProps<"div">;

export function TaskContent({ className, ...props }: TaskContentProps) {
  return (
    <div
      className={cn(
        "mt-2 space-y-1.5 border-l-2 border-[rgb(var(--color-border))] pl-3",
        className,
      )}
      {...props}
    />
  );
}

export type TaskItemProps = ComponentProps<"div">;

export function TaskItem({ className, ...props }: TaskItemProps) {
  return <div className={cn("text-[11px] sh-muted", className)} {...props} />;
}

export type TaskItemFileProps = ComponentProps<"span">;

export function TaskItemFile({ className, ...props }: TaskItemFileProps) {
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1 rounded-md border bg-black/[0.03] px-1.5 py-0.5 font-mono text-[10px] dark:bg-white/[0.04]",
        className,
      )}
      {...props}
    />
  );
}
