"use client";

import { useState } from "react";
import {
  IconCheck,
  IconLoader2,
  IconShieldLock,
  IconX,
} from "@tabler/icons-react";
import { useTranslations } from "next-intl";
import { toast } from "sonner";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  useMfaActivate,
  useMfaDisable,
  useMfaSetup,
  useMfaStatus,
} from "@/hooks/use-mfa";

/**
 * `MfaCard` — three-state TOTP enrollment widget:
 *
 *   * **Disabled** → "Enable MFA" button triggers `/mfa/setup` and the
 *     returned otpauth URI is surfaced as both a QR link and a base32 secret
 *     the user can type into an authenticator app.
 *   * **Pending** → ask for a 6-digit code to confirm; on success MFA is live.
 *   * **Enabled** → show a "Disable" button that re-prompts for the account
 *     password (backend enforces this too).
 */
export function MfaCard({ isSso }: { isSso: boolean }) {
  const t = useTranslations("settings.mfa");
  const status = useMfaStatus();
  const setup = useMfaSetup();
  const activate = useMfaActivate();
  const disable = useMfaDisable();

  const [setupData, setSetupData] = useState<{
    uri: string;
    secret: string;
  } | null>(null);
  const [code, setCode] = useState("");
  const [pw, setPw] = useState("");

  const startSetup = async () => {
    try {
      const data = await setup.mutateAsync();
      setSetupData({ uri: data.otpauth_uri, secret: data.secret });
      setCode("");
    } catch {
      toast.error(t("setupFailed"));
    }
  };

  const confirmActivate = async () => {
    try {
      await activate.mutateAsync({ code: code.trim() });
      toast.success(t("enabled"));
      setSetupData(null);
      setCode("");
    } catch {
      toast.error(t("badCode"));
    }
  };

  const confirmDisable = async () => {
    if (!pw.trim()) return;
    try {
      await disable.mutateAsync({ password: pw });
      toast.success(t("disabled"));
      setPw("");
    } catch {
      toast.error(t("disableFailed"));
    }
  };

  const st = status.data;

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <IconShieldLock className="size-4 text-amber-500" />
          {t("title")}
          {st?.enabled && (
            <Badge variant="success" className="ml-2">
              {t("badgeOn")}
            </Badge>
          )}
          {st?.pending && (
            <Badge variant="warning" className="ml-2">
              {t("badgePending")}
            </Badge>
          )}
        </CardTitle>
        <CardDescription>{t("description")}</CardDescription>
      </CardHeader>
      <CardContent className="space-y-3">
        {isSso && !st?.enabled && !st?.pending && (
          <p className="rounded-md border bg-black/5 p-2 text-xs sh-muted dark:bg-white/5">
            {t("ssoHint")}
          </p>
        )}

        {/* ── Disabled → can start ── */}
        {!st?.enabled && !st?.pending && !setupData && (
          <Button onClick={startSetup} disabled={setup.isPending}>
            {setup.isPending && <IconLoader2 className="size-4 animate-spin" />}
            {t("enable")}
          </Button>
        )}

        {/* ── Pending (just triggered setup) → QR + code input ── */}
        {setupData && (
          <div className="space-y-3 rounded-md border p-3">
            <p className="text-xs">{t("scanInstructions")}</p>
            <div className="flex flex-col items-center gap-2 rounded bg-white p-3">
              {/* QR server-rendered via a public service. Avoids shipping
                  a full qrcode.js dep for a one-shot enrollment screen. */}
              <img
                src={`https://api.qrserver.com/v1/create-qr-code/?size=180x180&data=${encodeURIComponent(
                  setupData.uri,
                )}`}
                alt="otpauth QR"
                className="size-44"
              />
              <div className="text-center font-mono text-[11px] break-all sh-muted">
                {setupData.secret}
              </div>
            </div>
            <div className="grid gap-1.5">
              <Label htmlFor="totp-code">{t("codeLabel")}</Label>
              <Input
                id="totp-code"
                value={code}
                onChange={(e) => setCode(e.target.value)}
                maxLength={8}
                placeholder="123456"
                inputMode="numeric"
                autoComplete="one-time-code"
              />
            </div>
            <div className="flex gap-2">
              <Button
                onClick={confirmActivate}
                disabled={activate.isPending || code.trim().length < 6}
              >
                {activate.isPending ? (
                  <IconLoader2 className="size-4 animate-spin" />
                ) : (
                  <IconCheck className="size-4" />
                )}
                {t("activate")}
              </Button>
              <Button
                variant="ghost"
                onClick={() => {
                  setSetupData(null);
                  setCode("");
                }}
              >
                <IconX className="size-4" />
                {t("cancel")}
              </Button>
            </div>
          </div>
        )}

        {/* ── Pending on server (page reload mid-enroll) ── */}
        {st?.pending && !setupData && (
          <div className="rounded-md border border-amber-300 bg-amber-50/50 p-3 text-xs dark:border-amber-700 dark:bg-amber-950/20">
            <div className="mb-2">{t("resumeHint")}</div>
            <Button size="sm" onClick={startSetup} disabled={setup.isPending}>
              {t("restart")}
            </Button>
          </div>
        )}

        {/* ── Enabled → disable path ── */}
        {st?.enabled && (
          <div className="grid gap-2 rounded-md border p-3">
            <Label htmlFor="mfa-disable-pw">{t("disablePwLabel")}</Label>
            <Input
              id="mfa-disable-pw"
              type="password"
              value={pw}
              onChange={(e) => setPw(e.target.value)}
              autoComplete="current-password"
            />
            <Button
              variant="destructive"
              onClick={confirmDisable}
              disabled={disable.isPending || !pw.trim()}
            >
              {disable.isPending && <IconLoader2 className="size-4 animate-spin" />}
              {t("disable")}
            </Button>
          </div>
        )}
      </CardContent>
    </Card>
  );
}
