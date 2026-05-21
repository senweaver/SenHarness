"use client";

import { useEffect, useMemo, useState } from "react";
import { Link } from "@/lib/navigation";
import { useRouter } from "@/lib/navigation"
import { useSearchParams } from "next/navigation";
import {
  IconBrandGithub,
  IconBrandGoogle,
  IconBrandWindows,
  IconShieldLock,
} from "@tabler/icons-react";
import { useTranslations } from "next-intl";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { api, API_BASE_URL } from "@/lib/api";
import { useOAuthProviders } from "@/hooks/use-mfa";
import type { TokenOut } from "@/types/api";
import { useAuthStore } from "@/stores/auth-store";

/**
 * Login page with three entry paths:
 *
 *   1. Password → POST /auth/login. If the identity has MFA on, the server
 *      returns 401 ``auth.mfa_required`` and we reveal a 6-digit code field.
 *      Resubmit with ``totp_code`` to finish.
 *   2. OAuth button → redirect to ``/auth/oauth/{provider}/start``. Backend
 *      bounces back to this page with ``?access_token=...&expires_at=...``
 *      after issuing the refresh cookie. We capture the access token into
 *      the auth store and redirect to `/`.
 *   3. Registration link.
 */
export default function LoginPage() {
  const t = useTranslations();
  const router = useRouter();
  const searchParams = useSearchParams();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [totpCode, setTotpCode] = useState("");
  const [mfaRequired, setMfaRequired] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const { data: oauth } = useOAuthProviders();

  // ── OAuth bounce-back: the callback route redirected the browser here with
  //    access_token + expires_at in the query string. Pick them up, store,
  //    then wipe the URL so a refresh doesn't re-apply stale tokens.
  useEffect(() => {
    const accessToken = searchParams.get("access_token");
    const expiresAt = searchParams.get("expires_at");
    const errParam = searchParams.get("error");
    if (accessToken && expiresAt) {
      useAuthStore.getState().setAccess(accessToken, expiresAt);
      router.replace("/");
      return;
    }
    if (errParam) {
      setError(errParam);
    }
  }, [searchParams, router]);

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError(null);
    setLoading(true);
    try {
      const tok = await api.post<TokenOut>(
        "/api/v1/auth/login",
        {
          email,
          password,
          totp_code: mfaRequired ? totpCode : undefined,
        },
        { skipAuth: true },
      );
      useAuthStore.getState().setAccess(tok.access_token, tok.expires_at);
      router.push("/");
    } catch (err: unknown) {
      const code =
        (err as { code?: string; message?: string }).code ?? "login_failed";
      if (code === "auth.mfa_required") {
        setMfaRequired(true);
        setError(null);
      } else if (code === "auth.mfa_invalid") {
        setError("mfa_invalid");
      } else {
        setError(code);
      }
    } finally {
      setLoading(false);
    }
  };

  const providers = useMemo(() => oauth?.providers ?? [], [oauth]);

  return (
    <main className="flex min-h-screen items-center justify-center px-4">
      <div className="w-full max-w-sm space-y-6">
        <div className="text-center">
          <div className="mx-auto mb-3 flex size-10 items-center justify-center rounded-md sh-primary text-sm font-bold">
            S
          </div>
          <h1 className="text-xl font-semibold">{t("auth.loginTitle")}</h1>
          <p className="mt-1 text-sm sh-muted">{t("auth.loginSubtitle")}</p>
        </div>

        <form onSubmit={submit} className="space-y-3" data-testid="login-form">
          <div className="space-y-1">
            <label className="text-xs sh-muted">{t("common.email")}</label>
            <Input
              type="email"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              required
              autoFocus
              disabled={mfaRequired}
              data-testid="login-email"
            />
          </div>
          <div className="space-y-1">
            <label className="text-xs sh-muted">{t("common.password")}</label>
            <Input
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              required
              disabled={mfaRequired}
              data-testid="login-password"
            />
          </div>

          {mfaRequired && (
            <div className="space-y-1 rounded-md border bg-amber-50/40 p-2 dark:bg-amber-950/20">
              <label className="flex items-center gap-1 text-xs">
                <IconShieldLock className="size-3 text-amber-500" />
                {t("auth.mfaCodeLabel")}
              </label>
              <Input
                inputMode="numeric"
                autoComplete="one-time-code"
                maxLength={8}
                value={totpCode}
                onChange={(e) => setTotpCode(e.target.value)}
                placeholder="123456"
                autoFocus
              />
            </div>
          )}

          {error && (
            <p className="text-xs text-red-500">
              {errorMessage(t, error)}
            </p>
          )}

          <Button
            type="submit"
            className="w-full"
            disabled={loading}
            data-testid="login-submit"
          >
            {loading ? t("common.loading") : t("common.signIn")}
          </Button>

          {providers.length > 0 && (
            <div className="space-y-2 pt-2">
              <div className="relative my-2 text-center text-[11px] sh-muted">
                <span className="relative z-10 bg-[rgb(var(--color-bg))] px-2">
                  {t("auth.orContinueWith")}
                </span>
                <span className="absolute inset-x-0 top-1/2 -z-0 border-t" />
              </div>
              {providers.includes("google") && (
                <OAuthButton provider="google" icon={<IconBrandGoogle className="size-4" />} />
              )}
              {providers.includes("github") && (
                <OAuthButton provider="github" icon={<IconBrandGithub className="size-4" />} />
              )}
              {providers.includes("microsoft") && (
                <OAuthButton provider="microsoft" icon={<IconBrandWindows className="size-4" />} />
              )}
            </div>
          )}

          <div className="text-center text-xs sh-muted">
            <Link href="/register" className="hover:underline">
              {t("common.signUp")}
            </Link>
          </div>
        </form>
      </div>
    </main>
  );
}

function OAuthButton({
  provider,
  icon,
}: {
  provider: "google" | "github" | "microsoft";
  icon: React.ReactNode;
}) {
  const t = useTranslations("auth");
  // Full-page redirect — the backend reads the "next" param to know where to
  // land the user after the IdP round-trip.
  const href = `${API_BASE_URL}/api/v1/auth/oauth/${provider}/start?next=${encodeURIComponent("/")}`;
  return (
    <Button type="button" variant="outline" className="w-full" asChild>
      <a href={href}>
        {icon}
        {t(`oauthSignIn.${provider}`)}
      </a>
    </Button>
  );
}

function errorMessage(t: (k: string) => string, code: string): string {
  if (code === "auth.mfa_invalid") return t("auth.mfaInvalid");
  if (code === "oauth_failed") return t("auth.oauthFailed");
  if (code === "oauth_profile_missing") return t("auth.oauthProfileMissing");
  if (code === "oauth_insufficient_scope") return t("auth.oauthScopeMissing");
  // V1 review: previously returned the raw backend code (e.g.
  // ``auth.invalid_credentials`` / ``rate_limit.exceeded``) which leaks
  // implementation detail and reads as a tech error. Map the high-frequency
  // ones, keep the code in parens for ops/debug.
  if (code === "auth.invalid_credentials") return t("auth.invalidCredentials");
  if (code === "auth.account_locked") return t("auth.accountLocked");
  if (code === "auth.account_disabled") return t("auth.accountDisabled");
  if (code === "rate_limit.exceeded") return t("auth.rateLimited");
  return `${t("auth.signInFailed")} (${code})`;
}
