"use client";

import { useTranslations } from "next-intl";

import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "@/components/ui/tooltip";
import { cn } from "@/lib/utils";
import type { GoalAlignmentScoreRead } from "@/types/api";

export type AlignmentBand = "high" | "medium" | "low";

export function classifyAlignment(
  score: number,
  threshold: number,
): AlignmentBand {
  if (score < threshold) return "low";
  // High band starts at threshold + 0.2; medium covers the buffer between.
  if (score >= threshold + 0.2) return "high";
  return "medium";
}

const BAND_TOKENS: Record<AlignmentBand, string> = {
  // Tailwind classes — keep palette-only so light / dark themes both work.
  high: "bg-green-500 ring-green-500/30",
  medium: "bg-amber-500 ring-amber-500/30",
  low: "bg-red-500 ring-red-500/30",
};

interface AlignmentDotProps {
  /** Most recent score for the assistant message. ``null`` = not scored yet. */
  score: GoalAlignmentScoreRead | null;
  /** Active goal threshold; falls back to 0.6 (matches backend default). */
  threshold?: number;
  className?: string;
}

export function AlignmentDot({
  score,
  threshold = 0.6,
  className,
}: AlignmentDotProps) {
  const t = useTranslations("sessionGoal");

  if (score == null) {
    return (
      <TooltipProvider delayDuration={150}>
        <Tooltip>
          <TooltipTrigger asChild>
            <span
              aria-label={t("alignmentNotScored")}
              className={cn(
                "inline-block h-2.5 w-2.5 rounded-full bg-muted-foreground/30 ring-2 ring-muted-foreground/10",
                className,
              )}
            />
          </TooltipTrigger>
          <TooltipContent side="top" className="max-w-xs text-xs">
            {t("alignmentNotScored")}
          </TooltipContent>
        </Tooltip>
      </TooltipProvider>
    );
  }

  const band = classifyAlignment(score.score, threshold);
  const labelKey =
    band === "high"
      ? "alignmentHigh"
      : band === "medium"
        ? "alignmentMedium"
        : "alignmentLow";
  const label = t(labelKey);
  const summary = t("alignmentTooltip", {
    score: score.score.toFixed(2),
    label,
  });

  return (
    <TooltipProvider delayDuration={150}>
      <Tooltip>
        <TooltipTrigger asChild>
          <button
            type="button"
            aria-label={summary}
            className={cn(
              "inline-flex h-2.5 w-2.5 items-center justify-center rounded-full ring-2 transition-transform hover:scale-110 focus-visible:outline-none focus-visible:ring-offset-1",
              BAND_TOKENS[band],
              className,
            )}
          />
        </TooltipTrigger>
        <TooltipContent side="top" className="max-w-xs space-y-1.5 text-xs">
          <div className="font-medium">{summary}</div>
          <div className="text-muted-foreground">
            {score.rationale ? score.rationale : t("alignmentNoRationale")}
          </div>
          {score.judged_by_model ? (
            <div className="text-[10px] uppercase tracking-wide text-muted-foreground/70">
              {t("alignmentJudgedBy", { model: score.judged_by_model })}
            </div>
          ) : null}
        </TooltipContent>
      </Tooltip>
    </TooltipProvider>
  );
}
