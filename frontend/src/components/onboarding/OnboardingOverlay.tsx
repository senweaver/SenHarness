"use client";

import { useCallback, useEffect } from "react";
import { useRouter } from "@/lib/navigation";
import { usePathname, useSearchParams } from "next/navigation";
import {
  IconArrowLeft,
  IconX,
} from "@tabler/icons-react";
import { useQueryClient } from "@tanstack/react-query";
import { useTranslations } from "next-intl";
import { toast } from "sonner";

import {
  Dialog,
  DialogContent,
} from "@/components/ui/dialog";
import { useAuthStore } from "@/stores/auth-store";
import { useOnboardingStore } from "@/stores/onboarding-store";
import { useAgentTerm } from "@/components/nav/AgentTermLabel";
import { useCompleteOnboarding } from "@/hooks/use-onboarding";

import { OnboardingProgressBar } from "./OnboardingProgressBar";
import { OnboardingStepAgent } from "./OnboardingStepAgent";
import { OnboardingStepDone } from "./OnboardingStepDone";
import { OnboardingStepProvider } from "./OnboardingStepProvider";
import { OnboardingStepWelcome } from "./OnboardingStepWelcome";
import { OnboardingStepWorkspace } from "./OnboardingStepWorkspace";

export function OnboardingOverlay() {
  const t = useTranslations("onboarding");
  const router = useRouter();
  const pathname = usePathname();
  const searchParams = useSearchParams();
  const token = useAuthStore((s) => s.accessToken);
  const queryClient = useQueryClient();
  const term = useAgentTerm();

  const open = useOnboardingStore((s) => s.open);
  const hydrated = useOnboardingStore((s) => s.hydrated);
  const step = useOnboardingStore((s) => s.step);
  const draft = useOnboardingStore((s) => s.draft);
  const hydrate = useOnboardingStore((s) => s.hydrate);
  const start = useOnboardingStore((s) => s.start);
  const close = useOnboardingStore((s) => s.close);
  const next = useOnboardingStore((s) => s.next);
  const back = useOnboardingStore((s) => s.back);

  const complete = useCompleteOnboarding();

  useEffect(() => {
    hydrate();
  }, [hydrate]);

  useEffect(() => {
    if (!hydrated || !token) return;
    if (searchParams.get("onboarding") === "1" && !open) {
      start();
    }
  }, [hydrated, token, searchParams, start, open]);

  const stripQueryFlag = useCallback(() => {
    if (!pathname) return;
    if (searchParams.get("onboarding") !== "1") return;
    const params = new URLSearchParams(searchParams.toString());
    params.delete("onboarding");
    const qs = params.toString();
    router.replace(`${pathname}${qs ? `?${qs}` : ""}` as never);
  }, [pathname, router, searchParams]);

  const finish = useCallback(async () => {
    try {
      await complete.mutateAsync();
      queryClient.invalidateQueries({ queryKey: ["me"] });
    } catch {
      toast.error(t("completeFailed"));
      return;
    }
    close({ clear: true });
    stripQueryFlag();
    if (draft.agentId) {
      router.push(`/chat?agent_id=${draft.agentId}` as never);
    } else {
      router.push("/");
    }
  }, [
    complete,
    queryClient,
    close,
    stripQueryFlag,
    draft.agentId,
    router,
    t,
  ]);

  const interrupt = useCallback(() => {
    close();
    stripQueryFlag();
  }, [close, stripQueryFlag]);

  const handleOpenChange = useCallback(
    (nextOpen: boolean) => {
      if (!nextOpen) interrupt();
    },
    [interrupt],
  );

  const welcomeSkip = useCallback(async () => {
    try {
      await complete.mutateAsync();
      queryClient.invalidateQueries({ queryKey: ["me"] });
    } catch {
      // ignore — onboarded_at is best-effort
    }
    close({ clear: true });
    stripQueryFlag();
  }, [close, complete, queryClient, stripQueryFlag]);

  if (!open) return null;

  const finishLabel = draft.agentId
    ? t("done.openAgentCta", { term })
    : t("done.openDashboardCta");

  return (
    <Dialog open={open} onOpenChange={handleOpenChange}>
      <DialogContent
        className="max-w-2xl gap-0 p-0"
        onEscapeKeyDown={(event) => {
          event.preventDefault();
          interrupt();
        }}
      >
        <div className="flex items-center justify-between border-b px-5 py-3">
          <button
            type="button"
            onClick={back}
            disabled={step === 1}
            aria-label={t("back")}
            className="flex size-7 items-center justify-center rounded-md sh-muted hover:bg-black/5 disabled:opacity-30 dark:hover:bg-white/10"
          >
            <IconArrowLeft className="size-4" />
          </button>
          <div className="min-w-0 flex-1 px-4">
            <OnboardingProgressBar step={step} />
          </div>
          <button
            type="button"
            onClick={interrupt}
            aria-label={t("interrupt")}
            className="flex size-7 items-center justify-center rounded-md sh-muted hover:bg-black/5 dark:hover:bg-white/10"
          >
            <IconX className="size-4" />
          </button>
        </div>

        <div className="min-h-[360px]">
          {step === 1 && (
            <OnboardingStepWelcome onNext={next} onSkip={welcomeSkip} />
          )}
          {step === 2 && <OnboardingStepWorkspace onNext={next} />}
          {step === 3 && <OnboardingStepProvider onNext={next} />}
          {step === 4 && <OnboardingStepAgent onNext={next} />}
          {step === 5 && (
            <OnboardingStepDone
              finishLabel={finishLabel}
              pending={complete.isPending}
              onFinish={finish}
            />
          )}
        </div>
      </DialogContent>
    </Dialog>
  );
}
