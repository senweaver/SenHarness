"use client";

/**
 * Inline-edit chip used by OverviewTab and RulesTab. The i18n
 * convention is ``settings.agents.detail.pickers.<key>`` — all
 * consumer pickers (`RuntimePicker`, `AutonomyPicker`, ...) read from
 * that subtree.
 */

import { IconCheck, IconChevronDown, IconLoader2 } from "@tabler/icons-react";
import { useState } from "react";

import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from "@/components/ui/popover";
import { cn } from "@/lib/utils";

export interface InlinePickerOption<TValue extends string> {
  value: TValue;
  label: string;
  description?: string;
}

export interface InlinePickerProps<TValue extends string> {
  label: string;
  value: TValue | null;
  options: InlinePickerOption<TValue>[];
  onChange: (next: TValue) => void | Promise<void>;
  /** Rendered when ``value`` matches no option. */
  placeholder?: string;
  pending?: boolean;
  disabled?: boolean;
  /** Optional ``className`` for the chip trigger. */
  className?: string;
}

/**
 * Generic inline-edit chip: clicking opens a popover with a vertical
 * list of options. Selection fires ``onChange`` and closes the popover.
 * Used by OverviewTab's runtime / autonomy / visibility / sandbox /
 * model rows so each row commits its change without an explicit
 * "Save" button.
 */
export function InlinePicker<TValue extends string>({
  label,
  value,
  options,
  onChange,
  placeholder = "—",
  pending = false,
  disabled = false,
  className,
}: InlinePickerProps<TValue>) {
  const [open, setOpen] = useState(false);
  const active = options.find((o) => o.value === value) ?? null;

  return (
    <Popover open={open} onOpenChange={(o) => !disabled && setOpen(o)}>
      <PopoverTrigger asChild>
        <button
          type="button"
          disabled={disabled}
          className={cn(
            "inline-flex items-center gap-1 rounded-sm border bg-transparent px-2 py-0.5 text-[12px] transition hover:bg-muted disabled:opacity-50",
            className,
          )}
          aria-label={label}
        >
          {pending ? <IconLoader2 className="size-3 animate-spin" /> : null}
          <span className="truncate">{active?.label ?? placeholder}</span>
          <IconChevronDown className="size-3 sh-muted" />
        </button>
      </PopoverTrigger>
      <PopoverContent align="end" className="w-56 p-0">
        <ul className="max-h-72 overflow-y-auto py-1">
          {options.map((opt) => {
            const selected = opt.value === value;
            return (
              <li key={opt.value}>
                <button
                  type="button"
                  className={cn(
                    "flex w-full items-start gap-2 px-3 py-1.5 text-left text-xs hover:bg-muted",
                    selected && "bg-primary/5",
                  )}
                  onClick={async () => {
                    setOpen(false);
                    if (!selected) await onChange(opt.value);
                  }}
                >
                  <span className="mt-0.5 flex size-3 flex-shrink-0 items-center justify-center">
                    {selected ? <IconCheck className="size-3" /> : null}
                  </span>
                  <span className="min-w-0 flex-1">
                    <span className="block font-medium">{opt.label}</span>
                    {opt.description ? (
                      <span className="mt-0.5 block text-[11px] sh-muted">
                        {opt.description}
                      </span>
                    ) : null}
                  </span>
                </button>
              </li>
            );
          })}
        </ul>
      </PopoverContent>
    </Popover>
  );
}
