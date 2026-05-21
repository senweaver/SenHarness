"use client";

import { useEffect, useState } from "react";
import { Link, useRouter } from "@/lib/navigation";
import { useSearchParams } from "next/navigation";
import { useTranslations } from "next-intl";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { api } from "@/lib/api";
import { useAuthStore } from "@/stores/auth-store";
import type {
  RegistrationModeOut,
  RegistrationResponse,
} from "@/types/api";

const SLUG_WARNING_KEY = "senharness:slug_warning";

export default function RegisterPage() {
  const t = useTranslations();
  const router = useRouter();
  const searchParams = useSearchParams();
  const [email, setEmail] = useState("");
  const [name, setName] = useState("");
  const [password, setPassword] = useState("");
  const [invitationCode, setInvitationCode] = useState(
    () => searchParams.get("invite") ?? "",
  );
  const [mode, setMode] = useState<RegistrationModeOut | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    let cancelled = false;
    api
      .get<RegistrationModeOut>("/api/v1/auth/registration-mode", { skipAuth: true })
      .then((res) => {
        if (!cancelled) setMode(res);
      })
      .catch(() => {
        if (!cancelled) {
          setMode({
            mode: "open_personal",
            invitation_required: false,
            requires_email_verification: false,
          });
        }
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError(null);
    if (mode?.mode === "closed") {
      setError("auth.registration_closed");
      return;
    }
    setLoading(true);
    try {
      const result = await api.post<RegistrationResponse>(
        "/api/v1/auth/register",
        {
          email,
          name,
          password,
          invitation_code: invitationCode.trim() || null,
        },
        { skipAuth: true },
      );

      if (result.workspace_slug_warning) {
        try {
          sessionStorage.setItem(
            SLUG_WARNING_KEY,
            JSON.stringify({
              slug: result.workspace?.slug ?? "",
              name: result.workspace?.name ?? "",
            }),
          );
        } catch {
          // sessionStorage disabled (Safari private mode) — toast skipped
        }
      }

      if (result.auto_login_tokens) {
        useAuthStore
          .getState()
          .setAccess(
            result.auto_login_tokens.access_token,
            result.auto_login_tokens.expires_at,
          );
        router.push("/?onboarding=1");
        return;
      }

      if (result.requires_email_verification) {
        const target = `/auth/verify-email-pending?email=${encodeURIComponent(result.email)}`;
        router.push(target);
        return;
      }

      // Identity created but the platform quota gate refused to provision
      // a personal workspace (M0.12 self-register opt-out). Surface a
      // dedicated query flag so /login can render the "contact admin"
      // copy instead of the regular registered-success line.
      const loginQuery =
        result.workspace == null
          ? "/login?registered=1&workspace_blocked=1"
          : "/login?registered=1";
      router.push(loginQuery);
    } catch (err: unknown) {
      const code =
        (err as { code?: string; message?: string }).code ??
        (err as { message?: string }).message ??
        "register_failed";
      setError(code);
    } finally {
      setLoading(false);
    }
  };

  const showInvitation =
    mode?.invitation_required ||
    invitationCode.length > 0 ||
    Boolean(searchParams.get("invite"));
  const blocked = mode?.mode === "closed";

  return (
    <main className="flex min-h-screen items-center justify-center px-4">
      <div className="w-full max-w-sm space-y-6">
        <div className="text-center">
          <div className="mx-auto mb-3 flex size-10 items-center justify-center rounded-md sh-primary text-sm font-bold">
            S
          </div>
          <h1 className="text-xl font-semibold">{t("auth.registerTitle")}</h1>
          <p className="mt-1 text-sm sh-muted">{t("auth.registerSubtitle")}</p>
        </div>

        {blocked && (
          <p className="rounded border border-amber-300 bg-amber-50 p-3 text-xs text-amber-800 dark:bg-amber-950/40 dark:text-amber-200">
            {t("register.registrationClosed")}
          </p>
        )}

        <form onSubmit={submit} className="space-y-3">
          <div className="space-y-1">
            <label className="text-xs sh-muted">{t("common.email")}</label>
            <Input
              type="email"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              required
              autoFocus
              disabled={blocked}
            />
          </div>
          <div className="space-y-1">
            <label className="text-xs sh-muted">{t("common.name")}</label>
            <Input
              value={name}
              onChange={(e) => setName(e.target.value)}
              required
              disabled={blocked}
            />
          </div>
          <div className="space-y-1">
            <label className="text-xs sh-muted">{t("common.password")}</label>
            <Input
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              required
              minLength={8}
              disabled={blocked}
            />
          </div>

          {showInvitation && (
            <div className="space-y-1">
              <label className="text-xs sh-muted">
                {mode?.invitation_required
                  ? t("register.invitationCodeRequired")
                  : t("register.invitationCodeOptional")}
              </label>
              <Input
                value={invitationCode}
                onChange={(e) => setInvitationCode(e.target.value)}
                required={mode?.invitation_required}
                disabled={blocked}
              />
            </div>
          )}

          {error && <p className="text-xs text-red-500">{errorMessage(t, error)}</p>}

          <Button type="submit" className="w-full" disabled={loading || blocked}>
            {loading ? t("common.loading") : t("common.signUp")}
          </Button>

          <div className="text-center text-xs sh-muted">
            <Link href="/login" className="hover:underline">
              {t("common.signIn")}
            </Link>
          </div>
        </form>
      </div>
    </main>
  );
}

function errorMessage(t: (k: string) => string, code: string): string {
  if (code === "auth.registration_closed") return t("register.registrationClosed");
  if (code === "auth.invitation_required") return t("register.invitationCodeRequired");
  if (code === "auth.email_taken") return t("register.emailTaken");
  if (code === "rate_limit.exceeded") return t("auth.rateLimited");
  return `${t("register.failed")} (${code})`;
}
