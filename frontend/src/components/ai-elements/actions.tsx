"use client";

/**
 * Adapted from Vercel AI SDK AI Elements (`Actions` primitive).
 *
 * Hover-revealed action toolbar attached to a Message. Children are arbitrary
 * action chips — the primitive is just spacing + show-on-hover styling.
 */

import type { ComponentPropsWithoutRef, ReactNode } from "react";

import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

interface ActionsProps extends ComponentPropsWithoutRef<"div"> {
  children: ReactNode;
}

export function Actions({ children, className, ...props }: ActionsProps) {
  return (
    <div
      className={cn(
        "flex items-center gap-1 text-[11px] sh-muted",
        // No-fade fallback for keyboard navigation; mouse users still see the
        // group-hover affordance via the parent's `group` class.
        "opacity-100 transition-opacity",
        className,
      )}
      {...props}
    >
      {children}
    </div>
  );
}

interface ActionProps extends ComponentPropsWithoutRef<"button"> {
  /** Tooltip / aria-label string. */
  label: string;
  /** Icon to render inside the action button. */
  icon: ReactNode;
}

export function Action({ label, icon, className, ...props }: ActionProps) {
  return (
    <Button
      type="button"
      size="icon"
      variant="ghost"
      aria-label={label}
      title={label}
      className={cn("size-6 [&_svg]:size-3", className)}
      {...props}
    >
      {icon}
    </Button>
  );
}
