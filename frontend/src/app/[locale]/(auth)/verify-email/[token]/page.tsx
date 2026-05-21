"use client";

import { useEffect, useRef, useState } from "react";
import { Link, useRouter } from "@/lib/navigation";
import { useTranslations } from "next-intl";
import { Button } from "@/components/ui/button";
import { api } from "@/lib/api";

type State = "verifying" | "success" | "expired" | "failed";

export default function VerifyEmailTokenPage({
  params,
}: {
  params: { token: string };
}) {
  const t = useTranslations("register");
  const tCommon = useTranslations("common");
  const router = useRouter();
  const [state, setState] = useState<State>("verifying");
  const [errorCode, setErrorCode] = useState<string | null>(null);
  const ranRef = useRef(false);

  useEffect(() => {
    if (ranRef.current) return;
    ranRef.current = true;
    api
      .post(`/api/v1/auth/verify-email/${encodeURIComponent(params.token)}`, undefined, {
        skipAuth: true,
      })
      .then(() => setState("success"))
      .catch((err: { code?: string }) => {
        const code = err?.code ?? "unknown";
        setErrorCode(code);
        if (
          code === "auth.verify_token_expired" ||
          code === "auth.verify_token_consumed"
        ) {
          setState("expired");
        } else {
          setState("failed");
        }
      });
  }, [params.token]);

  if (state === "verifying") {
    return (
      <main className="flex min-h-screen items-center justify-center px-4">
        <p className="text-sm sh-muted">{tCommon("loading")}</p>
      </main>
    );
  }

  if (state === "success") {
    return (
      <main className="flex min-h-screen items-center justify-center px-4">
        <div className="w-full max-w-md space-y-4 text-center">
          <div className="mx-auto flex size-12 items-center justify-center rounded-full sh-primary text-base font-bold">
            ✓
          </div>
          <h1 className="text-xl font-semibold">{t("verifyEmailSuccess")}</h1>
          <Button onClick={() => router.push("/login?registered=1")}>
            {tCommon("signIn")}
          </Button>
        </div>
      </main>
    );
  }

  return (
    <main className="flex min-h-screen items-center justify-center px-4">
      <div className="w-full max-w-md space-y-4 text-center">
        <div className="mx-auto flex size-12 items-center justify-center rounded-full bg-red-500/10 text-base font-bold text-red-500">
          !
        </div>
        <h1 className="text-xl font-semibold">{t("verifyEmailExpired")}</h1>
        <p className="text-xs sh-muted">{errorCode}</p>
        <Link href="/auth/verify-email-pending" className="text-sm underline">
          {t("verifyEmailResend")}
        </Link>
      </div>
    </main>
  );
}
