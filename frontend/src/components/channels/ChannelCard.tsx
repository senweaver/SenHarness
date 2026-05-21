"use client";

import { useState } from "react";
import {
  IconCopy,
  IconLogout,
  IconPlugConnected,
  IconQrcode,
  IconRefresh,
  IconTrash,
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
import { Switch } from "@/components/ui/switch";
import { useAgents } from "@/hooks/use-agents";
import {
  type ChannelRead,
  useDeleteChannel,
  useRotateChannelToken,
  useUpdateChannel,
  useWeChatQrLogin,
} from "@/hooks/use-channels";
import { API_BASE_URL } from "@/lib/api";
import { getChannelProvider } from "@/lib/channel-providers";
import { cn, relativeTime } from "@/lib/utils";
import { ChannelStatusBadge } from "@/components/channels/ChannelStatusBadge";
import { WeChatQrDialog } from "@/components/channels/WeChatQrDialog";

interface ChannelCardProps {
  ch: ChannelRead;
}

export function ChannelCard({ ch }: ChannelCardProps) {
  const t = useTranslations("settings.channels");
  const tWechat = useTranslations("settings.channels.wechatLogin");
  const tCommon = useTranslations("common");
  const { data: agents } = useAgents();
  const update = useUpdateChannel(ch.id);
  const remove = useDeleteChannel();
  const rotate = useRotateChannelToken(ch.id);
  const wechat = useWeChatQrLogin(ch.id);
  const brand = getChannelProvider(ch.kind);
  const Icon = brand.icon;

  const [qrOpen, setQrOpen] = useState(false);

  const bound = (agents ?? []).find((a) => a.id === ch.default_agent_id);
  const webhookUrl = `${API_BASE_URL}/api/v1/hooks/ingress/${ch.id}?token=${ch.inbound_token}`;
  const channelMode = String(
    (ch.metadata_json as { mode?: string } | null)?.mode ?? "",
  );
  const isStreamMode = channelMode !== "webhook";
  const hideWebhookUrl = ch.kind === "wechat" || isStreamMode;
  const hasBotToken = Boolean(
    typeof ch.config_json?.bot_token === "string" &&
      (ch.config_json.bot_token as string).length > 0,
  );
  const config = (ch.config_json as Record<string, unknown>) ?? {};
  const tSec = useTranslations("channelSecurity");
  const verifyOn = (config as { verify_signatures?: unknown }).verify_signatures !== false;
  const hmacSecretPresent = Boolean((config as { hmac_secret?: unknown }).hmac_secret);
  const showSignatureWarning =
    ch.kind === "webhook" && verifyOn && !hmacSecretPresent;

  const copyUrl = async () => {
    try {
      await navigator.clipboard.writeText(webhookUrl);
      toast.success(t("urlCopied"));
    } catch {
      toast.error(t("copyFailed"));
    }
  };

  const onToggle = async (enabled: boolean) => {
    try {
      await update.mutateAsync({ enabled });
    } catch {
      toast.error(t("updateFailed"));
    }
  };

  const onRotate = async () => {
    if (!confirm(t("rotateConfirm"))) return;
    try {
      await rotate.mutateAsync();
      toast.success(t("rotated"));
    } catch {
      toast.error(t("updateFailed"));
    }
  };

  const onDelete = async () => {
    if (!confirm(t("deleteConfirm"))) return;
    try {
      await remove.mutateAsync(ch.id);
      toast.success(t("deleted"));
    } catch {
      toast.error(t("updateFailed"));
    }
  };

  const onLogout = async () => {
    if (!confirm(t("wechatLogoutConfirm"))) return;
    try {
      await wechat.logout.mutateAsync();
      toast.success(tWechat("statusExpired"));
    } catch {
      toast.error(t("updateFailed"));
    }
  };

  return (
    <Card>
      <CardHeader className="pb-2">
        <div className="flex items-center gap-2">
          <span
            className={cn(
              "flex size-7 shrink-0 items-center justify-center rounded-md",
              brand.iconBg,
              brand.iconFg,
            )}
            aria-hidden
          >
            <Icon size={16} />
          </span>
          <CardTitle className="flex-1 truncate text-base">{ch.name}</CardTitle>
          <ChannelStatusBadge channelId={ch.id} kind={ch.kind} config={config} />
          <Badge variant="outline">
            <IconPlugConnected className="size-3" /> {ch.kind}
          </Badge>
          <Switch checked={ch.enabled} onCheckedChange={onToggle} />
        </div>
        <CardDescription className="text-[11px]">
          {t("bound")}: {bound?.name ?? "—"} · {relativeTime(ch.updated_at)}
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-2">
        {showSignatureWarning && (
          <div className="rounded-md border border-rose-500/40 bg-rose-500/10 px-3 py-1.5 text-[11px] text-rose-700 dark:text-rose-300">
            {tSec("signatureRequiredBanner")}
          </div>
        )}
        {ch.kind === "wechat" ? (
          <div className="flex flex-wrap items-center gap-2">
            <Button size="sm" onClick={() => setQrOpen(true)}>
              <IconQrcode className="size-4" />
              {hasBotToken ? tWechat("rescanCta") : tWechat("scanCta")}
            </Button>
            {hasBotToken && (
              <Button
                size="sm"
                variant="ghost"
                onClick={onLogout}
                disabled={wechat.logout.isPending}
              >
                <IconLogout className="size-4" />
                {tWechat("logoutCta")}
              </Button>
            )}
            <div className="flex-1" />
            <Button
              size="icon"
              variant="destructive"
              className="size-8"
              onClick={onDelete}
              disabled={remove.isPending}
              title={tCommon("delete")}
            >
              <IconTrash className="size-3.5" />
            </Button>
            <WeChatQrDialog
              channelId={ch.id}
              open={qrOpen}
              onOpenChange={setQrOpen}
            />
          </div>
        ) : hideWebhookUrl ? (
          <div className="flex items-start gap-2">
            <p className="flex-1 text-[11px] leading-relaxed sh-muted">
              {t.has(`setupHint.${ch.kind}`)
                ? t(`setupHint.${ch.kind}`)
                : ""}
            </p>
            <Button
              size="icon"
              variant="destructive"
              className="size-8 shrink-0"
              onClick={onDelete}
              disabled={remove.isPending}
              title={tCommon("delete")}
            >
              <IconTrash className="size-3.5" />
            </Button>
          </div>
        ) : (
          <div>
            <Label className="text-[11px] sh-muted">{t("webhookUrl")}</Label>
            <div className="flex items-center gap-1">
              <Input
                readOnly
                value={webhookUrl}
                className="font-mono text-[11px]"
              />
              <Button
                size="icon"
                variant="outline"
                className="size-8"
                onClick={copyUrl}
                title={t("copyUrl")}
              >
                <IconCopy className="size-3.5" />
              </Button>
              <Button
                size="icon"
                variant="outline"
                className="size-8"
                onClick={onRotate}
                disabled={rotate.isPending}
                title={t("rotateToken")}
              >
                <IconRefresh className="size-3.5" />
              </Button>
              <Button
                size="icon"
                variant="destructive"
                className="size-8"
                onClick={onDelete}
                disabled={remove.isPending}
                title={tCommon("delete")}
              >
                <IconTrash className="size-3.5" />
              </Button>
            </div>
            {t.has(`setupHint.${ch.kind}`) && (
              <p className="mt-1 text-[10px] sh-muted">
                {t(`setupHint.${ch.kind}`)}
              </p>
            )}
          </div>
        )}
      </CardContent>
    </Card>
  );
}
