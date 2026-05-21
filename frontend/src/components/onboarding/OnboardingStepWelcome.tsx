"use client";

import { IconArrowRight, IconSparkles } from "@tabler/icons-react";
import { useTranslations } from "next-intl";

import { Button } from "@/components/ui/button";

interface OnboardingStepWelcomeProps {
  onNext: () => void;
  onSkip: () => void;
}

export function OnboardingStepWelcome({
  onNext,
  onSkip,
}: OnboardingStepWelcomeProps) {
  const t = useTranslations("onboarding.welcome");
  return (
    <div className="flex flex-col items-center gap-6 px-6 py-10 text-center">
      <span className="flex size-16 items-center justify-center rounded-full bg-[rgb(var(--color-primary)/0.12)] text-[rgb(var(--color-primary))]">
        <IconSparkles className="size-7" />
      </span>
      <div className="space-y-2">
        <h2 className="text-xl font-semibold">{t("title")}</h2>
        <p className="text-sm sh-muted">{t("subtitle")}</p>
      </div>
      <div className="flex flex-col items-center gap-2">
        <Button size="lg" onClick={onNext}>
          {t("cta")}
          <IconArrowRight className="size-4" />
        </Button>
        <button
          type="button"
          onClick={onSkip}
          className="text-[11px] sh-muted hover:underline"
        >
          {t("skip")}
        </button>
      </div>
    </div>
  );
}
