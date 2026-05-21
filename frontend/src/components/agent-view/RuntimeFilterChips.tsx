"use client";

import { useTranslations } from "next-intl";

import { cn } from "@/lib/utils";

export type RuntimeFilter =
  | "all"
  | "healthy"
  | "stuck"
  | "orphan"
  | "thinking";

interface RuntimeFilterChipsProps {
  value: RuntimeFilter;
  counts: Record<RuntimeFilter, number>;
  onChange: (next: RuntimeFilter) => void;
}

const ORDER: RuntimeFilter[] = [
  "all",
  "healthy",
  "thinking",
  "stuck",
  "orphan",
];

export function RuntimeFilterChips({
  value,
  counts,
  onChange,
}: RuntimeFilterChipsProps) {
  const t = useTranslations("agentView.filters");
  return (
    <div className="flex flex-wrap gap-1.5">
      {ORDER.map((key) => (
        <button
          key={key}
          type="button"
          onClick={() => onChange(key)}
          className={cn(
            "rounded-full border px-3 py-1 text-xs transition",
            value === key
              ? "border-primary bg-primary/10 text-primary"
              : "hover:bg-muted",
          )}
        >
          {t(key)}
          <span className="ml-1 sh-muted">({counts[key] ?? 0})</span>
        </button>
      ))}
    </div>
  );
}
