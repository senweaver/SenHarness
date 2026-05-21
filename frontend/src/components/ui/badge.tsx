import * as React from "react";
import { cva, type VariantProps } from "class-variance-authority";
import { cn } from "@/lib/utils";

const badgeVariants = cva(
  "inline-flex items-center rounded-full px-2 py-0.5 text-[10px] font-medium leading-none",
  {
    variants: {
      variant: {
        default: "bg-black/10 text-[rgb(var(--color-fg))] dark:bg-white/15",
        primary: "sh-primary",
        success: "bg-green-500/15 text-green-700 dark:text-green-400",
        warning: "bg-amber-500/15 text-amber-700 dark:text-amber-400",
        danger: "bg-red-500/15 text-red-700 dark:text-red-400",
        // ``destructive`` kept as an alias for ``danger`` — matches the
        // shadcn/ui naming convention call sites in batch/* expected.
        // Using it routes to the same red palette.
        destructive: "bg-red-500/15 text-red-700 dark:text-red-400",
        outline: "border sh-muted",
      },
    },
    defaultVariants: { variant: "default" },
  },
);

export interface BadgeProps
  extends React.HTMLAttributes<HTMLSpanElement>,
    VariantProps<typeof badgeVariants> {}

export function Badge({ className, variant, ...props }: BadgeProps) {
  return <span className={cn(badgeVariants({ variant }), className)} {...props} />;
}
