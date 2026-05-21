"use client";

import { IconCheck, IconLoader2 } from "@tabler/icons-react";
import { useTranslations } from "next-intl";

import { Button } from "@/components/ui/button";

interface OnboardingStepDoneProps {
  finishLabel: string;
  pending: boolean;
  onFinish: () => void;
}

export function OnboardingStepDone({
  finishLabel,
  pending,
  onFinish,
}: OnboardingStepDoneProps) {
  const t = useTranslations("onboarding.done");
  return (
    <div className="flex flex-col items-center gap-6 px-6 py-10 text-center">
      <span className="flex size-16 items-center justify-center rounded-full bg-[rgb(var(--color-primary)/0.12)] text-[rgb(var(--color-primary))]">
        <IconCheck className="size-7" />
      </span>
      <div className="space-y-1">
        <h2 className="text-xl font-semibold">{t("title")}</h2>
        <p className="text-sm sh-muted">{t("subtitle")}</p>
      </div>
      <Button size="lg" onClick={onFinish} disabled={pending}>
        {pending && <IconLoader2 className="size-4 animate-spin" />}
        {finishLabel}
      </Button>
    </div>
  );
}
