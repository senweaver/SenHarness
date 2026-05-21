"use client";

/**
 * shadcn-style wrapper around `@radix-ui/react-tooltip`.
 *
 * Provides four exports:
 *   - ``TooltipProvider`` — must wrap any tree that renders tooltips. Mounted
 *     once high up (in ``app/layout``) so individual tooltips don't pay the
 *     mount cost. Default ``delayDuration`` keeps things snappy.
 *   - ``Tooltip``         — the root state container.
 *   - ``TooltipTrigger``  — the element the user hovers / focuses. Pair with
 *     ``asChild`` to keep ARIA attributes on the underlying control.
 *   - ``TooltipContent``  — the actual bubble. Inherits theme tokens via
 *     ``sh-card`` so dark mode comes for free.
 */

import * as React from "react";
import * as TooltipPrimitive from "@radix-ui/react-tooltip";

import { cn } from "@/lib/utils";

export const TooltipProvider = TooltipPrimitive.Provider;
export const Tooltip = TooltipPrimitive.Root;
export const TooltipTrigger = TooltipPrimitive.Trigger;

export const TooltipContent = React.forwardRef<
  React.ComponentRef<typeof TooltipPrimitive.Content>,
  React.ComponentPropsWithoutRef<typeof TooltipPrimitive.Content>
>(({ className, sideOffset = 6, ...props }, ref) => (
  <TooltipPrimitive.Portal>
    <TooltipPrimitive.Content
      ref={ref}
      sideOffset={sideOffset}
      className={cn(
        "z-50 max-w-xs overflow-hidden rounded-md border px-2 py-1 text-[11px] shadow-md",
        "bg-[rgb(var(--color-card))] text-[rgb(var(--color-fg))]",
        "data-[state=delayed-open]:animate-in data-[state=closed]:animate-out",
        "data-[state=delayed-open]:fade-in-0 data-[state=closed]:fade-out-0",
        "data-[state=delayed-open]:zoom-in-95 data-[state=closed]:zoom-out-95",
        className,
      )}
      {...props}
    />
  </TooltipPrimitive.Portal>
));
TooltipContent.displayName = "TooltipContent";

/**
 * Convenience wrapper — handles the boilerplate `Trigger + Content + Provider`
 * dance for the common case of "wrap this control with a label". Use the
 * primitives directly when you need full control (e.g. a tooltip whose body
 * is structured content, or one that closes on a custom action).
 *
 *     <SimpleTooltip label="Copy">
 *       <Button …>…</Button>
 *     </SimpleTooltip>
 */
export function SimpleTooltip({
  label,
  side = "top",
  align = "center",
  delayDuration = 200,
  children,
  contentClassName,
}: {
  label: React.ReactNode;
  side?: "top" | "right" | "bottom" | "left";
  align?: "start" | "center" | "end";
  delayDuration?: number;
  children: React.ReactNode;
  contentClassName?: string;
}) {
  if (label === null || label === undefined || label === "") {
    return <>{children}</>;
  }
  return (
    <TooltipProvider delayDuration={delayDuration} disableHoverableContent>
      <Tooltip>
        <TooltipTrigger asChild>{children}</TooltipTrigger>
        <TooltipContent
          side={side}
          align={align}
          className={contentClassName}
        >
          {label}
        </TooltipContent>
      </Tooltip>
    </TooltipProvider>
  );
}
