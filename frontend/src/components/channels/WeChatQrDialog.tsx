"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { IconCopy, IconLoader2, IconRefresh } from "@tabler/icons-react";
import { useTranslations } from "next-intl";
import QRCode from "qrcode";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Skeleton } from "@/components/ui/skeleton";
import { useWeChatQrLogin, type WeChatQrSession } from "@/hooks/use-channels";
import { cn } from "@/lib/utils";

interface WeChatQrDialogProps {
  channelId: string;
  open: boolean;
  onOpenChange: (open: boolean) => void;
}

type QrPhase = WeChatQrSession["status"];

const POLL_INTERVAL_MS = 1500;
const DEFAULT_EXPIRES_IN_S = 120;
const SUCCESS_AUTOCLOSE_MS = 800;

/**
 * QR-login dialog for the WeChat (iLink Bot) channel kind.
 *
 * The non-technical user flow: open the dialog from the channel card,
 * scan the rendered code with the WeChat account they want to bind,
 * confirm on the phone — the dialog auto-closes when the backend
 * reports ``status === "confirmed"`` (the bot_token has been written
 * back to the channel's config_json by then).
 *
 * Render-stability contract: this dialog kicks off a fresh QR session
 * on every open, then polls the backend every 1.5s. Both hooks
 * (``useWeChatQrLogin`` + ``useMutation``) hand back wrapper objects
 * whose identity flips on every parent render, so we route every
 * mutation/poll call through ``useRef``s. This means the effects are
 * keyed only on the dialog's own state (``open`` / ``session.qr_id`` /
 * ``phase``) and never re-arm just because the parent re-rendered.
 */
export function WeChatQrDialog({
  channelId,
  open,
  onOpenChange,
}: WeChatQrDialogProps) {
  const t = useTranslations("settings.channels.wechatLogin");
  const qc = useQueryClient();
  const { start, poll } = useWeChatQrLogin(channelId);

  const [session, setSession] = useState<WeChatQrSession | null>(null);
  const [phase, setPhase] = useState<QrPhase>("pending");
  const [secondsLeft, setSecondsLeft] = useState<number>(DEFAULT_EXPIRES_IN_S);
  const [errorMsg, setErrorMsg] = useState<string | null>(null);
  const [starting, setStarting] = useState(false);
  /** Locally-rendered QR data URI. The backend hands us the wechat
   *  ``liteapp.weixin.qq.com/q/...`` deep-link as a *string*, not a
   *  hosted PNG, so the browser has to encode it itself before the
   *  operator can scan. */
  const [qrDataUri, setQrDataUri] = useState<string>("");

  const pollTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const expiryTimerRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const cancelledRef = useRef(false);

  // Refs for everything React Query / props give us — see the
  // render-stability contract above.
  const startMutateRef = useRef(start.mutateAsync);
  const pollRef = useRef(poll);
  const onOpenChangeRef = useRef(onOpenChange);
  const qcRef = useRef(qc);
  useEffect(() => {
    startMutateRef.current = start.mutateAsync;
  }, [start.mutateAsync]);
  useEffect(() => {
    pollRef.current = poll;
  }, [poll]);
  useEffect(() => {
    onOpenChangeRef.current = onOpenChange;
  }, [onOpenChange]);
  useEffect(() => {
    qcRef.current = qc;
  }, [qc]);

  const stopTimers = useCallback(() => {
    if (pollTimerRef.current) {
      clearTimeout(pollTimerRef.current);
      pollTimerRef.current = null;
    }
    if (expiryTimerRef.current) {
      clearInterval(expiryTimerRef.current);
      expiryTimerRef.current = null;
    }
  }, []);

  // Map a raw backend error code into a stable token the rendered i18n
  // helper can interpolate. Anything that smells like "timed out" is
  // folded back into ``expired`` so the UI shows the friendlier
  // "QR expired — refresh" copy.
  const normalizeErrorCode = (raw: string | null | undefined): string => {
    const code = (raw ?? "").trim().toLowerCase();
    if (!code) return "unknown";
    if (code === "expired" || code === "timeout" || code === "timed_out") {
      return "expired";
    }
    return code;
  };

  const requestQr = useCallback(async () => {
    cancelledRef.current = false;
    stopTimers();
    setSession(null);
    setPhase("pending");
    setErrorMsg(null);
    setQrDataUri("");
    setStarting(true);
    try {
      const res = await startMutateRef.current();
      if (cancelledRef.current) return;
      setSession(res);
      setSecondsLeft(res.expires_in || DEFAULT_EXPIRES_IN_S);
      if (res.status === "error") {
        const code = normalizeErrorCode(res.error);
        if (code === "expired") {
          setPhase("expired");
        } else {
          setPhase("error");
          setErrorMsg(code);
        }
      } else {
        setPhase(res.status || "pending");
      }
    } catch (e) {
      if (cancelledRef.current) return;
      setPhase("error");
      // Network / 5xx — keep a stable code so the toast/label uses an
      // i18n key instead of leaking the raw upstream message.
      setErrorMsg(normalizeErrorCode(e instanceof Error ? e.message : null));
    } finally {
      setStarting(false);
    }
  }, [stopTimers]);

  // Render the upstream ``qrcode_image_data`` string into a scannable
  // PNG client-side. iLink hands us the wechat deep-link URL that the
  // QR is supposed to encode (not a hosted image), so the browser has
  // to do the encoding itself.
  useEffect(() => {
    const raw = (session?.qrcode_image_data ?? "").trim();
    if (!raw) {
      setQrDataUri("");
      return;
    }
    let cancelled = false;
    QRCode.toDataURL(raw, {
      errorCorrectionLevel: "M",
      margin: 2,
      width: 240,
    })
      .then((url) => {
        if (!cancelled) setQrDataUri(url);
      })
      .catch(() => {
        if (cancelled) return;
        // QR encoding failed — clear the preview; the dialog falls
        // back to the upstream-supplied image / numeric code.
        setQrDataUri("");
      });
    return () => {
      cancelled = true;
    };
  }, [session?.qrcode_image_data]);

  // Poll loop. Keyed only on the values the loop actually reacts to —
  // turning ``poll`` into a ref-read keeps a re-rendered parent from
  // tearing down and re-arming the timer on every paint.
  useEffect(() => {
    if (!open || !session?.qr_id) return;
    if (phase === "confirmed" || phase === "expired" || phase === "error") {
      return;
    }
    cancelledRef.current = false;
    let cancelled = false;
    const qrId = session.qr_id;

    const tick = async () => {
      try {
        const res = await pollRef.current(qrId);
        if (cancelled || cancelledRef.current) return;
        if (res.status === "error") {
          const code = normalizeErrorCode(res.error);
          if (code === "expired") {
            setPhase("expired");
          } else {
            setPhase("error");
            setErrorMsg(code);
          }
        } else {
          setPhase(res.status);
        }
        if (res.status === "confirmed") {
          await qcRef.current.invalidateQueries({ queryKey: ["channels"] });
          await qcRef.current.invalidateQueries({
            queryKey: ["channel-status", channelId],
          });
          setTimeout(() => {
            if (!cancelled && !cancelledRef.current) {
              onOpenChangeRef.current(false);
            }
          }, SUCCESS_AUTOCLOSE_MS);
          return;
        }
        if (res.status === "expired") return;
      } catch {
        if (cancelled || cancelledRef.current) return;
        // Transient poll failure (network blip, proxy hiccup) — the
        // next tick of the timer retries; no user-visible noise.
      }
      if (!cancelled && !cancelledRef.current) {
        pollTimerRef.current = setTimeout(tick, POLL_INTERVAL_MS);
      }
    };
    pollTimerRef.current = setTimeout(tick, POLL_INTERVAL_MS);
    return () => {
      cancelled = true;
      if (pollTimerRef.current) {
        clearTimeout(pollTimerRef.current);
        pollTimerRef.current = null;
      }
    };
  }, [open, session?.qr_id, phase, channelId]);

  // Live countdown for the small "valid for N seconds" hint.
  useEffect(() => {
    if (!open || !session?.qr_id) return;
    if (phase === "confirmed" || phase === "expired" || phase === "error") {
      return;
    }
    expiryTimerRef.current = setInterval(() => {
      setSecondsLeft((s) => {
        if (s <= 1) {
          setPhase("expired");
          return 0;
        }
        return s - 1;
      });
    }, 1000);
    return () => {
      if (expiryTimerRef.current) {
        clearInterval(expiryTimerRef.current);
        expiryTimerRef.current = null;
      }
    };
  }, [open, session?.qr_id, phase]);

  // Kick off a fresh QR every time the dialog opens; close tears down
  // timers + clears state so a reopen always starts clean.
  useEffect(() => {
    if (open) {
      void requestQr();
    } else {
      cancelledRef.current = true;
      stopTimers();
      setSession(null);
      setPhase("pending");
      setSecondsLeft(DEFAULT_EXPIRES_IN_S);
      setErrorMsg(null);
    }
    return () => {
      cancelledRef.current = true;
      stopTimers();
    };
  }, [open, requestQr, stopTimers]);

  const statusKey: Record<QrPhase, string> = {
    pending: "statusPending",
    scanned: "statusScanned",
    confirmed: "statusConfirmed",
    expired: "statusExpired",
    error: "statusError",
  };
  const statusToneClass: Record<QrPhase, string> = {
    pending: "text-[rgb(var(--color-foreground))]",
    scanned: "text-[#0082EF]",
    confirmed: "text-[#1AAD19]",
    expired: "text-[#F59E0B]",
    error: "text-[#EF4444]",
  };

  const showRefresh = phase === "expired" || phase === "error";
  /** Loading covers both the network round-trip (``POST /wechat/qr``)
   *  and the ~few-ms client-side QR encode that follows it. */
  const qrUrl = (session?.qrcode_image_data ?? "").trim();
  const isLoading = starting || (Boolean(qrUrl) && !qrDataUri);
  const dimQr = phase === "expired" || phase === "error";
  // Map the stable error code into the per-locale "Error: <copy>"
  // string. ``errors.<code>`` falls back to the raw code only when no
  // catalog entry exists so the UI never displays a kernel-flavoured
  // upstream message verbatim.
  const errorCode = errorMsg ?? "unknown";
  const errorCopy = t.has(`errors.${errorCode}`)
    ? t(`errors.${errorCode}`)
    : t("errors.unknown");
  const errorLabel = t("statusError", { error: errorCopy });

  const copyQrLink = async () => {
    if (!qrUrl) return;
    try {
      await navigator.clipboard.writeText(qrUrl);
      toast.success(t("linkCopied"));
    } catch {
      toast.error(t("copyFailed"));
    }
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>{t("dialogTitle")}</DialogTitle>
          <DialogDescription>{t("intro")}</DialogDescription>
        </DialogHeader>

        <div className="flex flex-col items-center gap-3">
          <div
            className={cn(
              "relative flex h-[220px] w-[220px] items-center justify-center rounded-md border bg-white p-2",
              dimQr && "opacity-50",
            )}
          >
            {isLoading ? (
              <Skeleton className="h-[200px] w-[200px]" />
            ) : qrDataUri ? (
              <img
                src={qrDataUri}
                alt="WeChat QR"
                className="h-full w-full object-contain"
              />
            ) : (
              <div className="px-3 text-center text-xs sh-muted">
                {errorLabel}
              </div>
            )}
          </div>

          <div
            className={cn("text-sm font-medium", statusToneClass[phase])}
            data-testid="wechat-qr-status"
          >
            {phase === "error" ? errorLabel : t(statusKey[phase])}
          </div>

          {(phase === "pending" || phase === "scanned") && (
            <div className="text-[11px] sh-muted">
              {t("expiresIn", { seconds: secondsLeft })}
            </div>
          )}

          {qrUrl && (
            <div className="w-full max-w-[320px] space-y-1.5">
              <Label className="text-[11px] sh-muted">{t("qrLinkLabel")}</Label>
              <div className="flex gap-1">
                <Input readOnly value={qrUrl} className="font-mono text-[10px]" />
                <Button
                  type="button"
                  size="icon"
                  variant="outline"
                  className="size-8 shrink-0"
                  onClick={() => void copyQrLink()}
                  title={t("copyQrLink")}
                >
                  <IconCopy className="size-3.5" />
                </Button>
              </div>
            </div>
          )}
        </div>

        <div className="mt-2 flex justify-end gap-2">
          {showRefresh && (
            <Button size="sm" onClick={() => void requestQr()} disabled={starting}>
              {starting ? (
                <IconLoader2 className="size-4 animate-spin" />
              ) : (
                <IconRefresh className="size-4" />
              )}
              {t("refresh")}
            </Button>
          )}
          <Button
            size="sm"
            variant="ghost"
            onClick={() => onOpenChange(false)}
            data-testid="wechat-qr-close"
          >
            {t("close")}
          </Button>
        </div>
      </DialogContent>
    </Dialog>
  );
}

export default WeChatQrDialog;
