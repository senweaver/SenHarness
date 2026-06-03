"use client";

import { useEffect, useMemo, useState } from "react";
import { IconExternalLink, IconLoader2 } from "@tabler/icons-react";
import { useTranslations } from "next-intl";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";
import { Switch } from "@/components/ui/switch";
import { useAgents } from "@/hooks/use-agents";
import {
  type ChannelKind,
  type ChannelKindMeta,
  type ChannelMode,
  type ChannelRead,
  type ChannelRoutingConfig,
  type SenderAllowlistRules,
  useChannelKinds,
  useCreateChannel,
} from "@/hooks/use-channels";
import { AdvancedSettings } from "@/components/channels/AdvancedSettings";
import {
  ChannelRoutingFields,
  DEFAULT_ROUTING_CONFIG,
} from "@/components/channels/ChannelRoutingFields";
import { ChipListField } from "@/components/channels/ChipListField";
import { SenderAllowlistPanel } from "@/components/channels/SenderAllowlistPanel";
import { SetupGuide } from "@/components/channels/SetupGuide";
import { WeChatQrDialog } from "@/components/channels/WeChatQrDialog";
import {
  getChannelProvider,
  isSensitiveField,
} from "@/lib/channel-providers";
import {
  defaultMode,
  isDualMode,
  pickOptionalFields,
  pickRequiredFields,
} from "@/lib/channel-mode-fields";
import { cn } from "@/lib/utils";

interface ChannelCreateFormProps {
  kind: ChannelKind;
  onBack?: () => void;
  onDone: () => void;
  lockedAgentId?: string;
  onCreated?: (channel: ChannelRead) => void;
}

export function ChannelCreateForm({
  kind,
  onBack,
  onDone,
  lockedAgentId,
  onCreated,
}: ChannelCreateFormProps) {
  const t = useTranslations("settings.channels");
  const tCommon = useTranslations("common");
  const { data: agents } = useAgents();
  const { data: kinds } = useChannelKinds();
  const create = useCreateChannel();

  const meta = useMemo<ChannelKindMeta | undefined>(
    () => kinds?.find((k) => k.kind === kind),
    [kinds, kind],
  );
  const brand = getChannelProvider(kind);

  const [name, setName] = useState("");
  const [agentId, setAgentId] = useState<string>(lockedAgentId ?? "");
  const [config, setConfig] = useState<Record<string, string>>({});
  const [mode, setMode] = useState<ChannelMode>("stream");
  const [pendingChannel, setPendingChannel] = useState<ChannelRead | null>(null);
  const [qrOpen, setQrOpen] = useState(false);
  const [senderRules, setSenderRules] = useState<SenderAllowlistRules>({
    mode: "allow_all",
  });
  const [discordGuilds, setDiscordGuilds] = useState<string[]>([]);
  const [discordAllowDms, setDiscordAllowDms] = useState(false);
  const [routing, setRouting] = useState<ChannelRoutingConfig>(
    DEFAULT_ROUTING_CONFIG,
  );

  useEffect(() => {
    if (meta) setMode(defaultMode(meta));
  }, [meta]);

  useEffect(() => {
    if (lockedAgentId) setAgentId(lockedAgentId);
  }, [lockedAgentId]);

  const setField = (field: string, value: string) =>
    setConfig((prev) => ({ ...prev, [field]: value }));

  const submit = async () => {
    if (!meta) return;
    // The default/primary agent is only mandatory for the legacy
    // ``agent`` scope; workspace/user scopes resolve a pool dynamically.
    const agentRequired = routing.bind_scope === "agent";
    if (!name.trim() || (agentRequired && !agentId)) {
      toast.error(t("missingFields"));
      return;
    }
    const requiredFields =
      kind === "wechat" && mode === "stream"
        ? []
        : pickRequiredFields(meta, mode);
    const missing = requiredFields.filter((f) => !(config[f] ?? "").trim());
    if (missing.length > 0) {
      toast.error(t("missingConfigFields", { fields: missing.join(", ") }));
      return;
    }
    try {
      const trimmed: Record<string, string | string[] | boolean> = {};
      for (const [k, v] of Object.entries(config)) {
        if (v.trim()) trimmed[k] = v.trim();
      }
      if (kind === "discord") {
        if (discordGuilds.length > 0) trimmed.allowed_guild_ids = discordGuilds;
        if (discordAllowDms) trimmed.allow_dms = true;
      }
      const created = await create.mutateAsync({
        name: name.trim(),
        kind,
        config_json: trimmed,
        default_agent_id: agentId || null,
        enabled: true,
        metadata_json: { mode },
        sender_allowlist_json:
          senderRules.mode === "allow_all" ? {} : senderRules,
        routing_config_json: routing,
      });
      toast.success(t("created"));
      onCreated?.(created);
      if (kind === "wechat" && mode === "stream") {
        setPendingChannel(created);
        setQrOpen(true);
      } else {
        onDone();
      }
    } catch (err) {
      const detail = (err as { body?: { detail?: unknown } } | undefined)?.body
        ?.detail;
      const code =
        typeof detail === "string"
          ? detail
          : (detail as { code?: string } | undefined)?.code;
      if (code === "channel.external_app_already_bound") {
        toast.error(t("externalAppAlreadyBound"));
      } else {
        toast.error(t("createFailed"));
      }
    }
  };

  if (!meta) return <Skeleton className="h-40" />;

  const Icon = brand.icon;
  const visibleRequired = pickRequiredFields(meta, mode);
  const visibleOptional = pickOptionalFields(meta, mode).filter(
    (f) =>
      f !== "verify_signatures" &&
      f !== "allowed_guild_ids" &&
      f !== "allow_dms",
  );

  return (
    <div className="space-y-4">
      <div className="flex items-center gap-3">
        <span
          className={cn(
            "flex size-10 shrink-0 items-center justify-center rounded-md",
            brand.iconBg,
            brand.iconFg,
          )}
          aria-hidden
        >
          <Icon size={22} />
        </span>
        <div className="min-w-0 flex-1">
          <h3 className="truncate text-base font-medium">{meta.display_name}</h3>
          <p className="truncate text-[11px] sh-muted">{meta.description}</p>
        </div>
        {meta.docs_url && (
          <Button
            variant="ghost"
            size="sm"
            asChild
            className="text-[11px] sh-muted"
          >
            <a href={meta.docs_url} target="_blank" rel="noreferrer">
              <IconExternalLink className="size-3.5" />
              {t("docs")}
            </a>
          </Button>
        )}
        {onBack && (
          <Button variant="ghost" size="sm" onClick={onBack}>
            {tCommon("back")}
          </Button>
        )}
      </div>

      <SetupGuide kind={kind} />

      <div className="grid gap-3 sm:grid-cols-2">
        <div className="grid gap-1.5">
          <Label>{t("form.name")}</Label>
          <Input
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder={t("form.namePlaceholder")}
          />
        </div>
        {!lockedAgentId && (
          <div className="grid gap-1.5">
            <Label>
              {t("form.agent")}
              {routing.bind_scope !== "agent" && (
                <span className="ml-1 text-[10px] sh-muted">
                  {t("optional")}
                </span>
              )}
            </Label>
            <Select value={agentId} onValueChange={setAgentId}>
              <SelectTrigger>
                <SelectValue placeholder={t("form.agentPlaceholder")} />
              </SelectTrigger>
              <SelectContent>
                {(agents ?? []).map((a) => (
                  <SelectItem key={a.id} value={a.id}>
                    {a.name}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
        )}

        {visibleRequired.map((field) => (
          <ConfigField
            key={`req-${field}`}
            field={field}
            value={config[field] ?? ""}
            onChange={(v) => setField(field, v)}
            required
          />
        ))}
        {visibleOptional.map((field) => (
          <ConfigField
            key={`opt-${field}`}
            field={field}
            value={config[field] ?? ""}
            onChange={(v) => setField(field, v)}
          />
        ))}

        <div className="space-y-2 border-t pt-3 sm:col-span-2">
          <div>
            <h4 className="text-[12px] font-medium">{t("routing.title")}</h4>
            <p className="text-[11px] sh-muted">{t("routing.description")}</p>
          </div>
          <ChannelRoutingFields
            value={routing}
            onChange={setRouting}
            agents={agents}
          />
        </div>

        <div className="mt-1 flex justify-end gap-2 sm:col-span-2">
          <Button variant="ghost" onClick={onDone} disabled={create.isPending}>
            {tCommon("cancel")}
          </Button>
          <Button onClick={submit} disabled={create.isPending}>
            {create.isPending && <IconLoader2 className="size-4 animate-spin" />}
            {tCommon("save")}
          </Button>
        </div>
      </div>

      <ChannelSecuritySection
        kind={kind}
        config={config}
        onFieldChange={setField}
        senderRules={senderRules}
        onSenderRulesChange={setSenderRules}
        discordGuilds={discordGuilds}
        onDiscordGuildsChange={setDiscordGuilds}
        discordAllowDms={discordAllowDms}
        onDiscordAllowDmsChange={setDiscordAllowDms}
      />

      {(isDualMode(meta) || mode === "webhook") && (
        <AdvancedSettings
          meta={meta}
          mode={mode}
          onModeChange={setMode}
          config={config}
          onFieldChange={setField}
        />
      )}

      {pendingChannel && (
        <WeChatQrDialog
          channelId={pendingChannel.id}
          open={qrOpen}
          onOpenChange={(o) => {
            setQrOpen(o);
            if (!o) {
              onDone();
            }
          }}
        />
      )}
    </div>
  );
}

function ConfigField({
  field,
  value,
  onChange,
  required = false,
}: {
  field: string;
  value: string;
  onChange: (v: string) => void;
  required?: boolean;
}) {
  const t = useTranslations("settings.channels");
  const sensitive = isSensitiveField(field);
  const labelKey = `field.${field}`;
  const hintKey = `fieldHint.${field}`;
  const label = t.has(labelKey) ? t(labelKey) : field;
  const hint = t.has(hintKey) ? t(hintKey) : "";

  return (
    <div className="grid gap-1.5 sm:col-span-2">
      <Label>
        {label}
        {!required && (
          <span className="ml-1 text-[10px] sh-muted">{t("optional")}</span>
        )}
      </Label>
      <Input
        value={value}
        onChange={(e) => onChange(e.target.value)}
        type={sensitive ? "password" : "text"}
        placeholder={hint || undefined}
        autoComplete="off"
      />
      {hint && <p className="text-[11px] sh-muted">{hint}</p>}
    </div>
  );
}

interface ChannelSecuritySectionProps {
  kind: ChannelKind;
  config: Record<string, string>;
  onFieldChange: (field: string, value: string) => void;
  senderRules: SenderAllowlistRules;
  onSenderRulesChange: (next: SenderAllowlistRules) => void;
  discordGuilds: string[];
  onDiscordGuildsChange: (next: string[]) => void;
  discordAllowDms: boolean;
  onDiscordAllowDmsChange: (next: boolean) => void;
}

function ChannelSecuritySection({
  kind,
  config,
  onFieldChange,
  senderRules,
  onSenderRulesChange,
  discordGuilds,
  onDiscordGuildsChange,
  discordAllowDms,
  onDiscordAllowDmsChange,
}: ChannelSecuritySectionProps) {
  const tSec = useTranslations("channelSecurity");
  const verifyOn = (config.verify_signatures ?? "true") !== "false";

  return (
    <div className="space-y-3">
      {kind === "webhook" && (
        <div className="space-y-2 rounded-md border bg-[rgb(var(--color-card))] p-3">
          <div className="flex items-center justify-between">
            <div>
              <Label className="text-[12px] font-medium">
                {tSec("verifySignaturesLabel")}
              </Label>
              <p className="mt-0.5 text-[11px] sh-muted">
                {tSec("verifySignaturesHint")}
              </p>
            </div>
            <Switch
              checked={verifyOn}
              onCheckedChange={(checked) =>
                onFieldChange("verify_signatures", checked ? "true" : "false")
              }
            />
          </div>
          {verifyOn ? (
            <>
              <div className="rounded-md border border-rose-500/40 bg-rose-500/10 px-3 py-2 text-[11px] text-rose-700 dark:text-rose-300">
                {tSec("hmacSecretRequired")}
              </div>
              <div className="grid gap-1.5">
                <Label className="text-[12px]">{tSec("hmacSecretLabel")}</Label>
                <Input
                  value={config.hmac_secret ?? ""}
                  onChange={(e) => onFieldChange("hmac_secret", e.target.value)}
                  type="password"
                  placeholder={tSec("hmacSecretPlaceholder")}
                  autoComplete="off"
                />
              </div>
            </>
          ) : (
            <p className="text-[11px] sh-muted">{tSec("verifySignaturesOff")}</p>
          )}
        </div>
      )}

      {kind === "discord" && (
        <div className="space-y-3 rounded-md border bg-[rgb(var(--color-card))] p-3">
          <div>
            <Label className="text-[12px] font-medium">
              {tSec("discordSecurityTitle")}
            </Label>
            <p className="mt-0.5 text-[11px] sh-muted">
              {tSec("discordSecurityHint")}
            </p>
          </div>
          <ChipListField
            label={tSec("discordGuildIdsLabel")}
            hint={tSec("discordGuildIdsHint")}
            placeholder="123456789012345678"
            values={discordGuilds}
            onChange={onDiscordGuildsChange}
          />
          <div className="flex items-center justify-between">
            <div>
              <Label className="text-[12px]">{tSec("discordAllowDmsLabel")}</Label>
              <p className="text-[11px] sh-muted">{tSec("discordAllowDmsHint")}</p>
            </div>
            <Switch
              checked={discordAllowDms}
              onCheckedChange={onDiscordAllowDmsChange}
            />
          </div>
        </div>
      )}

      {kind === "slack" && (
        <div className="space-y-2 rounded-md border bg-[rgb(var(--color-card))] p-3">
          <div>
            <Label className="text-[12px] font-medium">
              {tSec("slackTeamIdLabel")}
            </Label>
            <p className="mt-0.5 text-[11px] sh-muted">{tSec("slackTeamIdHint")}</p>
          </div>
          <Input
            value={config.expected_team_id ?? ""}
            onChange={(e) => onFieldChange("expected_team_id", e.target.value)}
            placeholder="T0000000000"
          />
        </div>
      )}

      <SenderAllowlistPanel rules={senderRules} onChange={onSenderRulesChange} />
    </div>
  );
}
