"use client";

import { useEffect, useState } from "react";
import { IconCopy, IconKey, IconLoader2 } from "@tabler/icons-react";
import { useTranslations } from "next-intl";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { useAgents } from "@/hooks/use-agents";
import {
  type ChannelBindCode,
  type ChannelRead,
  type ChannelRoutingConfig,
  useCreateBindCode,
  useUpdateChannel,
} from "@/hooks/use-channels";
import {
  ChannelRoutingFields,
  DEFAULT_ROUTING_CONFIG,
} from "@/components/channels/ChannelRoutingFields";
import { ChannelBindingRules } from "@/components/channels/ChannelBindingRules";

/** Sentinel Select value standing in for "no primary agent". */
const NO_PRIMARY = "__none__";

interface ChannelSettingsDialogProps {
  channel: ChannelRead;
  open: boolean;
  onOpenChange: (open: boolean) => void;
}

function mergeRouting(
  stored: Partial<ChannelRoutingConfig> | undefined,
): ChannelRoutingConfig {
  return { ...DEFAULT_ROUTING_CONFIG, ...(stored ?? {}) };
}

export function ChannelSettingsDialog({
  channel,
  open,
  onOpenChange,
}: ChannelSettingsDialogProps) {
  const t = useTranslations("settings.channels");
  const tCommon = useTranslations("common");
  const { data: agents } = useAgents();
  const update = useUpdateChannel(channel.id);
  const bindCode = useCreateBindCode(channel.id);

  const [routing, setRouting] = useState<ChannelRoutingConfig>(
    mergeRouting(channel.routing_config_json),
  );
  const [primaryAgentId, setPrimaryAgentId] = useState<string>(
    channel.default_agent_id ?? NO_PRIMARY,
  );
  const [code, setCode] = useState<ChannelBindCode | null>(null);
  const [remaining, setRemaining] = useState(0);

  // Re-seed local state whenever a fresh channel is opened so the dialog
  // never shows a stale routing blob from a previously-edited channel.
  useEffect(() => {
    if (!open) return;
    setRouting(mergeRouting(channel.routing_config_json));
    setPrimaryAgentId(channel.default_agent_id ?? NO_PRIMARY);
    setCode(null);
  }, [open, channel]);

  // Live TTL countdown for a freshly minted bind code; clear the code
  // once it lapses so the operator regenerates instead of handing out a
  // stale one. Anchored on wall-clock so it stays accurate if the tab
  // throttles the interval in the background. ``remaining`` is seeded in
  // ``generateCode`` so this effect never sets state synchronously.
  useEffect(() => {
    if (!code) return;
    const expiresAt = Date.now() + code.ttl_seconds * 1000;
    const timer = setInterval(() => {
      const left = Math.ceil((expiresAt - Date.now()) / 1000);
      if (left <= 0) {
        clearInterval(timer);
        setRemaining(0);
        setCode(null);
      } else {
        setRemaining(left);
      }
    }, 1000);
    return () => clearInterval(timer);
  }, [code]);

  const save = async () => {
    if (routing.bind_scope === "agent" && primaryAgentId === NO_PRIMARY) {
      toast.error(t("routing.primaryRequired"));
      return;
    }
    try {
      await update.mutateAsync({
        default_agent_id: primaryAgentId === NO_PRIMARY ? null : primaryAgentId,
        routing_config_json: routing,
      });
      toast.success(t("routing.saved"));
      onOpenChange(false);
    } catch {
      toast.error(t("updateFailed"));
    }
  };

  const generateCode = async () => {
    try {
      const minted = await bindCode.mutateAsync();
      setRemaining(minted.ttl_seconds);
      setCode(minted);
    } catch {
      toast.error(t("bindCode.failed"));
    }
  };

  const copyCode = async () => {
    if (!code) return;
    try {
      await navigator.clipboard.writeText(code.code);
      toast.success(t("bindCode.copied"));
    } catch {
      toast.error(t("copyFailed"));
    }
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-h-[85vh] overflow-y-auto">
        <DialogHeader>
          <DialogTitle>{t("routing.dialogTitle")}</DialogTitle>
          <DialogDescription>{t("routing.description")}</DialogDescription>
        </DialogHeader>

        <div className="space-y-4">
          <div className="grid gap-1.5">
            <Label className="text-[12px] font-medium">
              {t("routing.primaryAgent")}
              {routing.bind_scope !== "agent" && (
                <span className="ml-1 text-[10px] sh-muted">
                  {t("optional")}
                </span>
              )}
            </Label>
            <Select value={primaryAgentId} onValueChange={setPrimaryAgentId}>
              <SelectTrigger>
                <SelectValue placeholder={t("form.agentPlaceholder")} />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value={NO_PRIMARY}>
                  {t("routing.noPrimary")}
                </SelectItem>
                {(agents ?? []).map((a) => (
                  <SelectItem key={a.id} value={a.id}>
                    {a.name}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>

          <ChannelRoutingFields
            value={routing}
            onChange={setRouting}
            agents={agents}
          />

          {routing.bind_scope !== "agent" && (
            <ChannelBindingRules channelId={channel.id} />
          )}

          <div className="space-y-2 rounded-md border bg-[rgb(var(--color-card))] p-3">
            <div>
              <Label className="text-[12px] font-medium">
                {t("bindCode.title")}
              </Label>
              <p className="mt-0.5 text-[11px] sh-muted">
                {t("bindCode.description")}
              </p>
            </div>
            {code ? (
              <div className="space-y-1.5">
                <div className="flex items-center gap-1">
                  <Input
                    readOnly
                    value={code.code}
                    className="font-mono text-base tracking-[0.3em]"
                  />
                  <Button
                    size="icon"
                    variant="outline"
                    className="size-9 shrink-0"
                    onClick={copyCode}
                    title={t("bindCode.copy")}
                  >
                    <IconCopy className="size-3.5" />
                  </Button>
                </div>
                <p className="text-[11px] sh-muted">
                  {t("bindCode.ttl", {
                    seconds: remaining,
                  })}
                </p>
                <p className="text-[11px] sh-muted">
                  {t("bindCode.instruction", { code: code.code })}
                </p>
              </div>
            ) : (
              <Button
                size="sm"
                variant="outline"
                onClick={generateCode}
                disabled={bindCode.isPending}
              >
                {bindCode.isPending ? (
                  <IconLoader2 className="size-4 animate-spin" />
                ) : (
                  <IconKey className="size-4" />
                )}
                {t("bindCode.generate")}
              </Button>
            )}
          </div>
        </div>

        <DialogFooter>
          <Button
            variant="ghost"
            onClick={() => onOpenChange(false)}
            disabled={update.isPending}
          >
            {tCommon("cancel")}
          </Button>
          <Button onClick={save} disabled={update.isPending}>
            {update.isPending && (
              <IconLoader2 className="size-4 animate-spin" />
            )}
            {tCommon("save")}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

export default ChannelSettingsDialog;
