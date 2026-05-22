"use client";

import { useState } from "react";
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
import { PageHeader } from "@/components/ui/page-header";
import {
  useConsumePairing,
  useInitiatePairing,
  useLogicalThread,
  useLogicalThreads,
  useThreadBindings,
  useUnbindChannel,
} from "@/hooks/use-logical-threads";

export default function CrossPlatformSettingsPage() {
  const t = useTranslations("crossPlatform");
  const { data: list, isLoading } = useLogicalThreads({ limit: 50 });
  const [selectedThreadId, setSelectedThreadId] = useState<string | null>(null);
  const [pairingCode, setPairingCode] = useState<string | null>(null);
  const [pairingExpires, setPairingExpires] = useState<string | null>(null);
  const [redeemCode, setRedeemCode] = useState("");

  const items = list?.items ?? [];
  const activeThreadId = selectedThreadId ?? items[0]?.id ?? null;

  const { data: thread } = useLogicalThread(activeThreadId);
  const { data: bindings } = useThreadBindings(activeThreadId);
  const initiate = useInitiatePairing();
  const consume = useConsumePairing();
  const unbind = useUnbindChannel(activeThreadId);

  async function handleIssueCode() {
    try {
      const res = await initiate.mutateAsync({});
      setPairingCode(res.code);
      setPairingExpires(res.expires_at);
      toast.success(t("toast.codeIssued"));
    } catch {
      toast.error(t("toast.codeIssueFailed"));
    }
  }

  async function handleRedeem() {
    if (!redeemCode.match(/^\d{6}$/)) {
      toast.error(t("toast.codeInvalid"));
      return;
    }
    try {
      const res = await consume.mutateAsync({ code: redeemCode });
      toast.success(
        t("toast.codeConsumed", {
          paired: res.bindings_paired,
          merged: res.threads_merged,
        }),
      );
      setRedeemCode("");
    } catch {
      toast.error(t("toast.codeConsumeFailed"));
    }
  }

  return (
    <div className="space-y-6">
      <PageHeader title={t("title")} description={t("description")} />

      <Card>
        <CardHeader>
          <CardTitle>{t("threads.title")}</CardTitle>
          <CardDescription>{t("threads.description")}</CardDescription>
        </CardHeader>
        <CardContent>
          {isLoading ? (
            <p className="text-sm sh-muted">{t("loading")}</p>
          ) : items.length === 0 ? (
            <p className="text-sm sh-muted">{t("threads.empty")}</p>
          ) : (
            <ul className="divide-y">
              {items.map((it) => (
                <li
                  key={it.id}
                  className={`flex cursor-pointer items-center justify-between py-2 ${
                    activeThreadId === it.id ? "bg-muted/40" : ""
                  }`}
                  onClick={() => setSelectedThreadId(it.id)}
                >
                  <div>
                    <p className="text-sm font-medium">
                      {it.label ?? t("threads.unlabeled")}
                    </p>
                    <p className="sh-muted text-xs">
                      {t("threads.lastActivity", {
                        when: new Date(it.last_activity_at).toLocaleString(),
                      })}
                    </p>
                  </div>
                  <Badge variant="outline">
                    {t("threads.idShort", { id: it.id.slice(0, 8) })}
                  </Badge>
                </li>
              ))}
            </ul>
          )}
        </CardContent>
      </Card>

      {activeThreadId ? (
        <Card>
          <CardHeader>
            <CardTitle>{t("bindings.title")}</CardTitle>
            <CardDescription>{t("bindings.description")}</CardDescription>
          </CardHeader>
          <CardContent>
            {!bindings || bindings.length === 0 ? (
              <p className="text-sm sh-muted">{t("bindings.empty")}</p>
            ) : (
              <ul className="divide-y">
                {bindings.map((b) => (
                  <li
                    key={b.id}
                    className="flex items-center justify-between py-2"
                  >
                    <div>
                      <p className="text-sm font-medium">
                        {b.channel_name ?? t("bindings.webOrCli")}
                        <span className="sh-muted ml-2 text-xs">
                          {b.channel_kind ?? ""}
                        </span>
                      </p>
                      <p className="sh-muted text-xs">
                        {t("bindings.externalUserHint", {
                          ext: b.external_user_id ?? "—",
                        })}
                      </p>
                    </div>
                    <div className="flex items-center gap-2">
                      <Badge
                        variant={b.is_paired ? "default" : "outline"}
                      >
                        {b.is_paired
                          ? t("bindings.paired")
                          : t("bindings.unpaired")}
                      </Badge>
                      <Button
                        size="sm"
                        variant="outline"
                        onClick={() =>
                          unbind.mutate(
                            { binding_id: b.id },
                            {
                              onSuccess: () =>
                                toast.success(t("toast.unbinded")),
                              onError: () =>
                                toast.error(t("toast.unbindFailed")),
                            },
                          )
                        }
                      >
                        {t("bindings.unbind")}
                      </Button>
                    </div>
                  </li>
                ))}
              </ul>
            )}
          </CardContent>
        </Card>
      ) : null}

      <Card>
        <CardHeader>
          <CardTitle>{t("pair.title")}</CardTitle>
          <CardDescription>{t("pair.description")}</CardDescription>
        </CardHeader>
        <CardContent className="space-y-6">
          <div className="space-y-3">
            <p className="text-sm font-medium">{t("pair.issueTitle")}</p>
            <p className="sh-muted text-xs">{t("pair.issueDescription")}</p>
            <Button
              size="sm"
              onClick={handleIssueCode}
              disabled={initiate.isPending}
            >
              {initiate.isPending
                ? t("pair.issuing")
                : t("pair.issueButton")}
            </Button>
            {pairingCode ? (
              <div className="bg-muted/30 rounded-md border p-3 text-sm">
                <p className="font-mono text-2xl tracking-widest">
                  {pairingCode}
                </p>
                <p className="sh-muted mt-1 text-xs">
                  {t("pair.expiresAt", {
                    when: pairingExpires
                      ? new Date(pairingExpires).toLocaleTimeString()
                      : "",
                  })}
                </p>
              </div>
            ) : null}
          </div>

          <div className="space-y-3">
            <p className="text-sm font-medium">{t("pair.redeemTitle")}</p>
            <p className="sh-muted text-xs">{t("pair.redeemDescription")}</p>
            <div className="flex gap-2">
              <Label htmlFor="pair-code" className="sr-only">
                {t("pair.codeLabel")}
              </Label>
              <Input
                id="pair-code"
                inputMode="numeric"
                pattern="[0-9]*"
                maxLength={6}
                value={redeemCode}
                onChange={(e) =>
                  setRedeemCode(e.target.value.replace(/[^0-9]/g, ""))
                }
                placeholder="000000"
                className="font-mono tracking-widest"
              />
              <Button
                onClick={handleRedeem}
                disabled={consume.isPending || redeemCode.length !== 6}
              >
                {consume.isPending
                  ? t("pair.redeeming")
                  : t("pair.redeemButton")}
              </Button>
            </div>
          </div>
        </CardContent>
      </Card>
    </div>
  );
}
