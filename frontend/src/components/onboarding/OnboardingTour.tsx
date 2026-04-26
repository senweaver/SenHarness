"use client";

import { useEffect, useState, useCallback } from "react";
import { useRouter } from "@/lib/navigation";
import { useTranslations } from "next-intl";
import {
  IconArrowRight,
  IconCheck,
  IconMessageCircle2,
  IconRobot,
  IconSparkles,
} from "@tabler/icons-react";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { useAuthStore } from "@/stores/auth-store";
import { useMe } from "@/hooks/use-me";

const STORAGE_KEY = "senharness:onboarding:v1";
const STORAGE_DONE = "done";

type Step = {
  icon: typeof IconSparkles;
  titleKey: string;
  bodyKey: string;
  ctaKey: string;
  navigateTo?: string;
};

const STEPS: Step[] = [
  {
    icon: IconSparkles,
    titleKey: "step1.title",
    bodyKey: "step1.body",
    ctaKey: "step1.cta",
  },
  {
    icon: IconRobot,
    titleKey: "step2.title",
    bodyKey: "step2.body",
    ctaKey: "step2.cta",
    navigateTo: "/agents",
  },
  {
    icon: IconMessageCircle2,
    titleKey: "step3.title",
    bodyKey: "step3.body",
    ctaKey: "step3.cta",
    navigateTo: "/chat",
  },
];

export function OnboardingTour() {
  const t = useTranslations("onboarding");
  const router = useRouter();
  const token = useAuthStore((s) => s.accessToken);
  const { data: me } = useMe();

  const [open, setOpen] = useState(false);
  const [stepIndex, setStepIndex] = useState(0);

  // Trigger only after auth is settled AND the user has at least one
  // workspace. We deliberately check localStorage at mount time so a
  // returning user never sees the tour twice; clearing the key from
  // settings/profile re-arms it.
  useEffect(() => {
    if (!token || !me) return;
    if (typeof window === "undefined") return;
    try {
      if (localStorage.getItem(STORAGE_KEY) === STORAGE_DONE) return;
    } catch {
      // localStorage disabled (e.g. Safari private mode) → still show; just
      // can't persist completion. Acceptable: worst case, the user dismisses
      // it once per session.
    }
    setStepIndex(0);
    setOpen(true);
  }, [token, me]);

  const markDone = useCallback(() => {
    try {
      localStorage.setItem(STORAGE_KEY, STORAGE_DONE);
    } catch {
      // ignore — see comment above
    }
  }, []);

  const handleSkip = useCallback(() => {
    markDone();
    setOpen(false);
  }, [markDone]);

  const handleNext = useCallback(() => {
    const current = STEPS[stepIndex];
    if (!current) return;
    const isLast = stepIndex === STEPS.length - 1;
    if (current.navigateTo) {
      router.push(current.navigateTo);
    }
    if (isLast) {
      markDone();
      setOpen(false);
      return;
    }
    setStepIndex((i) => Math.min(i + 1, STEPS.length - 1));
  }, [stepIndex, router, markDone]);

  if (!open) return null;
  const step = STEPS[stepIndex];
  if (!step) return null;
  const Icon = step.icon;
  const isLast = stepIndex === STEPS.length - 1;

  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        // Closing via overlay click counts as skip — record completion so
        // we don't pester the user on every reload.
        if (!next) handleSkip();
        else setOpen(next);
      }}
    >
      <DialogContent className="max-w-md">
        <DialogHeader>
          <div className="flex items-center gap-2">
            <span className="inline-flex size-8 items-center justify-center rounded-full bg-primary/10 text-primary">
              <Icon className="size-5" />
            </span>
            <DialogTitle>{t(step.titleKey)}</DialogTitle>
          </div>
          <DialogDescription className="pt-2 text-sm leading-relaxed sh-muted">
            {t(step.bodyKey)}
          </DialogDescription>
        </DialogHeader>

        {/* Step dots — gives the user a sense of "3 quick steps" without
            forcing them to count clicks. */}
        <div className="flex items-center justify-center gap-1.5 py-1">
          {STEPS.map((_, i) => (
            <span
              key={i}
              className={
                "h-1.5 w-6 rounded-full transition-colors " +
                (i === stepIndex ? "bg-primary" : "bg-muted")
              }
              aria-hidden
            />
          ))}
        </div>

        <DialogFooter>
          <Button variant="ghost" onClick={handleSkip} type="button">
            {t("skip")}
          </Button>
          <Button onClick={handleNext} type="button">
            {t(step.ctaKey)}
            {isLast ? (
              <IconCheck className="ml-1 size-4" />
            ) : (
              <IconArrowRight className="ml-1 size-4" />
            )}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

/**
 * Programmatic re-trigger — call from settings/profile to let the user
 * reopen the tour. Returns true if local storage was reset successfully.
 */
export function resetOnboardingTour(): boolean {
  try {
    localStorage.removeItem(STORAGE_KEY);
    return true;
  } catch {
    return false;
  }
}
