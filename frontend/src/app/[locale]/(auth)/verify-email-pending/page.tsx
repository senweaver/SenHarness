"use client";

import { useEffect, useState } from "react";
import { Link } from "@/lib/navigation";
import { useSearchParams } from "next/navigation";
import { useTranslations } from "next-intl";
import { Button } from "@/components/ui/button";
import { api } from "@/lib/api";

const RESEND_COOLDOWN_S = 60;

export default function VerifyEmailPendingPage() {
  const t = useTranslations("register");
  const tCommon = useTranslations("common");
  const searchParams = useSearchParams();
  const email = searchParams.get("email") ?? "";

  const [cooldown, setCooldown] = useState(0);
  const [busy, setBusy] = useState(false);
  const [feedback, setFeedback] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (cooldown <= 0) return;
    const id = setTimeout(() => setCooldown((s) => Math.max(0, s - 1)), 1000);
    return () => clearTimeout(id);
  }, [cooldown]);

  const resend = async () => {
    if (!email || busy || cooldown > 0) return;
    setBusy(true);
    setError(null);
    setFeedback(null);
    try {
      await api.post(
        "/api/v1/auth/resend-verification",
        { email },
        { skipAuth: true },
      );
      setFeedback(t("verifyEmailResendOk"));
      setCooldown(RESEND_COOLDOWN_S);
    } catch (err: unknown) {
      const code = (err as { code?: string }).code ?? "unknown";
      if (code === "rate_limit.exceeded") {
        setError(t("verifyEmailResendRateLimited"));
        setCooldown(RESEND_COOLDOWN_S);
      } else {
        setError(`${t("verifyEmailResendFailed")} (${code})`);
      }
    } finally {
      setBusy(false);
    }
  };

  return (
    <main className="flex min-h-screen items-center justify-center px-4">
      <div className="w-full max-w-md space-y-6 text-center">
        <div className="mx-auto flex size-12 items-center justify-center rounded-full sh-primary text-base font-bold">
          ✉
        </div>
        <h1 className="text-xl font-semibold">{t("verifyEmailTitle")}</h1>
        <p className="text-sm sh-muted">
          {email ? t("verifyEmailSent", { email }) : t("verifyEmailSentNoEmail")}
        </p>

        <div className="space-y-2">
          <Button
            type="button"
            variant="outline"
            className="w-full"
            disabled={busy || cooldown > 0 || !email}
            onClick={resend}
          >
            {cooldown > 0
              ? `${t("verifyEmailResend")} (${cooldown}s)`
              : t("verifyEmailResend")}
          </Button>
          {feedback && <p className="text-xs text-emerald-600">{feedback}</p>}
          {error && <p className="text-xs text-red-500">{error}</p>}
        </div>

        <div className="space-y-1 text-xs sh-muted">
          <p>{t("verifyEmailWrongAddressHint")}</p>
          <Link href="/login" className="underline">
            {tCommon("signIn")}
          </Link>
        </div>
      </div>
    </main>
  );
}
