"use client";

import type { ReactNode } from "react";
import { useState } from "react";
import { IconChevronDown } from "@tabler/icons-react";
import { useTranslations } from "next-intl";

import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  type ChannelKindMeta,
  type ChannelMode,
} from "@/hooks/use-channels";
import { isSensitiveField } from "@/lib/channel-providers";
import {
  defaultMode,
  isDualMode,
  pickWebhookOnlyFields,
} from "@/lib/channel-mode-fields";
import { cn } from "@/lib/utils";

interface AdvancedSettingsProps {
  meta: ChannelKindMeta;
  mode: ChannelMode;
  onModeChange: (mode: ChannelMode) => void;
  config: Record<string, string>;
  onFieldChange: (field: string, value: string) => void;
  /** Optional slot rendered above the mode picker — used by the
   *  ChannelCard to show the inbound webhook URL inside the same
   *  collapsed section. */
  extraSlot?: ReactNode;
  className?: string;
}

/**
 * Folded-up "advanced" panel that hides the mode toggle + every
 * webhook-only field behind one click. New operators see neither the
 * word "webhook" nor the secrets that go with it; experts can expand
 * the panel and switch modes.
 *
 * Always renders a header (so operators with an old config can still
 * find the toggle) — the panel body adapts based on whether the
 * provider supports both modes and whether the active mode has any
 * extra fields.
 */
export function AdvancedSettings({
  meta,
  mode,
  onModeChange,
  config,
  onFieldChange,
  extraSlot,
  className,
}: AdvancedSettingsProps) {
  const t = useTranslations("settings.channels.advanced");
  const tField = useTranslations("settings.channels.field");
  const tHint = useTranslations("settings.channels.fieldHint");

  const [open, setOpen] = useState(false);
  const showModePicker = isDualMode(meta);
  const fallbackMode = defaultMode(meta);
  const activeMode: ChannelMode = mode || fallbackMode;
  const webhookOnly = pickWebhookOnlyFields(meta);
  const webhookOnlyVisible = activeMode === "webhook" ? webhookOnly : [];
  const streamUnavailable = meta.stream_available === false;

  return (
    <div
      className={cn(
        "rounded-md border bg-[rgb(var(--color-card))]",
        className,
      )}
    >
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="flex w-full items-center gap-2 px-3 py-2 text-left text-xs sh-muted"
        aria-expanded={open}
      >
        <IconChevronDown
          className={cn(
            "size-3.5 transition-transform",
            open && "rotate-180",
          )}
        />
        <span>{t("toggle")}</span>
      </button>

      {open && (
        <div className="space-y-3 border-t px-3 py-3">
          {extraSlot}

          {showModePicker && (
            <div>
              <Label className="text-[12px] font-medium">
                {t("modeLabel")}
              </Label>
              <div className="mt-1.5 grid grid-cols-1 gap-2 sm:grid-cols-2">
                <ModeCard
                  selected={activeMode === "stream"}
                  disabled={streamUnavailable}
                  onClick={() => onModeChange("stream")}
                  title={t("modeStream")}
                  hint={
                    streamUnavailable
                      ? t("streamUnavailable")
                      : t("modeStreamHint")
                  }
                />
                <ModeCard
                  selected={activeMode === "webhook"}
                  onClick={() => onModeChange("webhook")}
                  title={t("modeWebhook")}
                  hint={t("modeWebhookHint")}
                />
              </div>
            </div>
          )}

          {webhookOnlyVisible.length > 0 && (
            <div className="space-y-2">
              {webhookOnlyVisible.map((field) => {
                const sensitive = isSensitiveField(field);
                const labelKey = field;
                const hintKey = field;
                const label = tField.has(labelKey)
                  ? tField(labelKey)
                  : field;
                const hint = tHint.has(hintKey) ? tHint(hintKey) : "";
                return (
                  <div key={field} className="grid gap-1.5">
                    <Label className="text-[12px]">{label}</Label>
                    <Input
                      value={config[field] ?? ""}
                      onChange={(e) => onFieldChange(field, e.target.value)}
                      type={sensitive ? "password" : "text"}
                      placeholder={hint || undefined}
                      autoComplete="off"
                    />
                    {hint && (
                      <p className="text-[11px] sh-muted">{hint}</p>
                    )}
                  </div>
                );
              })}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

interface ModeCardProps {
  selected: boolean;
  disabled?: boolean;
  onClick: () => void;
  title: string;
  hint: string;
}

function ModeCard({ selected, disabled, onClick, title, hint }: ModeCardProps) {
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled}
      className={cn(
        "flex flex-col gap-1 rounded-md border p-2 text-left text-xs transition",
        "hover:border-[rgb(var(--color-primary))]",
        selected && "border-[rgb(var(--color-primary))] bg-[rgb(var(--color-primary))]/5",
        disabled && "cursor-not-allowed opacity-60 hover:border-[rgb(var(--color-border))]",
      )}
      aria-pressed={selected}
    >
      <span className="font-medium">{title}</span>
      <span className="text-[11px] sh-muted">{hint}</span>
    </button>
  );
}

export default AdvancedSettings;
