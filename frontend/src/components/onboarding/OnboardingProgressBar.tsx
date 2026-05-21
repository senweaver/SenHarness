"use client";

import { useTranslations } from "next-intl";

import { cn } from "@/lib/utils";
import type { OnboardingStep } from "@/stores/onboarding-store";

const STEPS: OnboardingStep[] = [1, 2, 3, 4, 5];

interface OnboardingProgressBarProps {
  step: OnboardingStep;
}

export function OnboardingProgressBar({ step }: OnboardingProgressBarProps) {
  const t = useTranslations("onboarding.steps");
  const labels: Record<OnboardingStep, string> = {
    1: t("welcome"),
    2: t("workspace"),
    3: t("provider"),
    4: t("agent"),
    5: t("done"),
  };

  return (
    <ol className="flex items-center gap-2">
      {STEPS.map((s, index) => {
        const isComplete = s < step;
        const isCurrent = s === step;
        return (
          <li key={s} className="flex flex-1 items-center gap-2">
            <span
              className={cn(
                "flex size-6 shrink-0 items-center justify-center rounded-full text-[11px] font-semibold",
                isCurrent
                  ? "sh-primary"
                  : isComplete
                    ? "bg-[rgb(var(--color-primary)/0.25)] text-[rgb(var(--color-primary))]"
                    : "bg-black/5 sh-muted dark:bg-white/10",
              )}
              aria-current={isCurrent ? "step" : undefined}
            >
              {s}
            </span>
            <span
              className={cn(
                "hidden text-[11px] sm:inline-block",
                isCurrent ? "font-semibold" : "sh-muted",
              )}
            >
              {labels[s]}
            </span>
            {index < STEPS.length - 1 && (
              <span
                className={cn(
                  "h-px flex-1 rounded-full",
                  isComplete
                    ? "bg-[rgb(var(--color-primary))]"
                    : "bg-black/10 dark:bg-white/10",
                )}
                aria-hidden
              />
            )}
          </li>
        );
      })}
    </ol>
  );
}
