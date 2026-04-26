"use client";

/**
 * Minimal dependency-free bar chart. Renders one bar per data point, height
 * proportional to value/max. Hover shows a tooltip. Designed for small
 * footprint; for richer charts swap in Recharts / Nivo later.
 */

import { useMemo } from "react";
import { cn } from "@/lib/utils";

export interface SparkBarPoint {
  label: string;
  value: number;
  tooltip?: string;
}

export function SparkBar({
  data,
  className,
  height = 120,
  color = "rgb(var(--color-primary))",
  emptyLabel,
  formatValue,
}: {
  data: SparkBarPoint[];
  className?: string;
  height?: number;
  color?: string;
  emptyLabel?: string;
  formatValue?: (v: number) => string;
}) {
  const max = useMemo(
    () => data.reduce((m, p) => (p.value > m ? p.value : m), 0),
    [data],
  );

  if (!data.length || max === 0) {
    return (
      <div
        className={cn(
          "flex items-center justify-center rounded-md border border-dashed text-xs sh-muted",
          className,
        )}
        style={{ height }}
      >
        {emptyLabel ?? "no data"}
      </div>
    );
  }

  return (
    <div
      className={cn("flex items-end gap-[2px]", className)}
      style={{ height }}
      role="img"
    >
      {data.map((p, idx) => {
        const h = Math.max((p.value / max) * height, p.value > 0 ? 2 : 0);
        const tooltip =
          p.tooltip ??
          `${p.label}: ${formatValue ? formatValue(p.value) : p.value}`;
        return (
          <div
            key={`${p.label}-${idx}`}
            className="relative flex-1 rounded-sm transition-opacity hover:opacity-80"
            style={{ height: `${h}px`, backgroundColor: color }}
            title={tooltip}
            aria-label={tooltip}
          />
        );
      })}
    </div>
  );
}
