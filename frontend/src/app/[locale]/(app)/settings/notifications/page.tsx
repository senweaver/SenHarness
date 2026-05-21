"use client";

import { useEffect, useMemo, useState } from "react";
import {
  IconBell,
  IconInbox,
  IconLock,
  IconMailFast,
  IconMoon,
} from "@tabler/icons-react";
import { useLocale, useTranslations } from "next-intl";
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
import { PageHeader } from "@/components/ui/page-header";
import { Switch } from "@/components/ui/switch";
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip";
import {
  useNotificationPrefs,
  useUpdateNotificationPrefs,
} from "@/hooks/use-notifications";
import { Link } from "@/lib/navigation";
import { cn } from "@/lib/utils";
import type {
  NotificationEventDescriptor,
  NotificationPrefEntry,
} from "@/types/api";

type ChannelKind = "in_app" | "email";

interface DraftState {
  prefs: Record<string, NotificationPrefEntry>;
  /** ISO timestamp string for the global mute window, or empty when unset. */
  mutedUntil: string;
}

export default function NotificationPrefsPage() {
  const t = useTranslations("notification");
  const tPrefs = useTranslations("notification.prefs");
  const tNs = useTranslations();
  const locale = useLocale();
  const { data, isLoading } = useNotificationPrefs();
  const update = useUpdateNotificationPrefs();
  const [draft, setDraft] = useState<DraftState>({
    prefs: {},
    mutedUntil: "",
  });

  useEffect(() => {
    if (!data) return;
    const seeded: Record<string, NotificationPrefEntry> = {};
    for (const desc of data.catalog) {
      const existing = data.prefs[desc.key];
      if (existing) {
        seeded[desc.key] = {
          channels: [...existing.channels],
          muted: existing.muted,
        };
      } else {
        seeded[desc.key] = {
          channels: [...desc.default_channels],
          muted: false,
        };
      }
    }
    setDraft({
      prefs: seeded,
      mutedUntil: isoToLocalDatetimeInputValue(data._global?.muted_until),
    });
  }, [data]);

  const dirty = useMemo(() => {
    if (!data) return false;
    for (const desc of data.catalog) {
      const a = draft.prefs[desc.key];
      const b = data.prefs[desc.key];
      if (!a) continue;
      const bChans = b?.channels ?? [...desc.default_channels];
      const bMuted = b?.muted ?? false;
      if (a.muted !== bMuted) return true;
      if (a.channels.slice().sort().join(",") !== bChans.slice().sort().join(","))
        return true;
    }
    const currentMuted = isoToLocalDatetimeInputValue(
      data._global?.muted_until,
    );
    if (draft.mutedUntil !== currentMuted) return true;
    return false;
  }, [data, draft]);

  if (isLoading || !data) {
    return (
      <div>
        <PageHeader title={t("prefsTitle")} description={t("prefsDesc")} />
        <p className="text-sm sh-muted">{t("loading")}</p>
      </div>
    );
  }

  const toggleChannel = (key: string, channel: ChannelKind) => {
    setDraft((prev) => {
      const cur = prev.prefs[key];
      if (!cur) return prev;
      const has = cur.channels.includes(channel);
      const next = has
        ? cur.channels.filter((c) => c !== channel)
        : [...cur.channels, channel];
      return {
        ...prev,
        prefs: { ...prev.prefs, [key]: { ...cur, channels: next } },
      };
    });
  };

  const toggleMute = (key: string) => {
    setDraft((prev) => {
      const cur = prev.prefs[key];
      if (!cur) return prev;
      return {
        ...prev,
        prefs: { ...prev.prefs, [key]: { ...cur, muted: !cur.muted } },
      };
    });
  };

  const onSave = async () => {
    try {
      const mutedIso = localDatetimeInputToIso(draft.mutedUntil);
      await update.mutateAsync({
        prefs: draft.prefs,
        _global: { muted_until: mutedIso },
      });
      toast.success(t("savedToast"));
    } catch (err) {
      toast.error(String((err as Error).message ?? err));
    }
  };

  const quietHoursMutedUntilIso = localDatetimeInputToIso(draft.mutedUntil);
  const quietHoursActive = quietHoursMutedUntilIso !== null;

  return (
    <div>
      <PageHeader
        title={t("prefsTitle")}
        description={t("prefsDesc")}
        actions={
          <Button asChild size="sm" variant="ghost">
            <Link href="/notifications">
              <IconInbox className="size-4" />
              {tPrefs("openInbox")}
            </Link>
          </Button>
        }
      />

      <Card className="mb-3">
        <CardHeader>
          <CardTitle className="flex items-center gap-2 text-base">
            <IconMoon className="size-4" /> {tPrefs("quietHoursTitle")}
            {quietHoursActive && (
              <Badge variant="outline">{tPrefs("quietHoursActive")}</Badge>
            )}
          </CardTitle>
          <CardDescription>{tPrefs("quietHoursDesc")}</CardDescription>
        </CardHeader>
        <CardContent>
          <div className="flex flex-wrap items-end gap-3">
            <div className="flex-1 min-w-[220px]">
              <label className="mb-1 block text-[11px] sh-muted">
                {tPrefs("quietHoursLabel")}
              </label>
              <Input
                type="datetime-local"
                value={draft.mutedUntil}
                onChange={(e) =>
                  setDraft((prev) => ({ ...prev, mutedUntil: e.target.value }))
                }
                placeholder={tPrefs("quietHoursPlaceholder")}
              />
            </div>
            <Button
              variant="outline"
              size="sm"
              disabled={!draft.mutedUntil}
              onClick={() =>
                setDraft((prev) => ({ ...prev, mutedUntil: "" }))
              }
            >
              {tPrefs("quietHoursClear")}
            </Button>
          </div>
          {quietHoursActive && data._global?.muted_until && (
            <p className="mt-2 text-[11px] sh-muted">
              {tPrefs("quietHoursMutedUntil", {
                time: new Date(data._global.muted_until).toLocaleString(locale),
              })}
            </p>
          )}
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2 text-base">
            <IconBell className="size-4" /> {t("eventListTitle")}
          </CardTitle>
          <CardDescription>{t("eventListDesc")}</CardDescription>
        </CardHeader>
        <CardContent className="space-y-3">
          {data.catalog.map((desc) => {
            const entry = draft.prefs[desc.key] ?? {
              channels: [...desc.default_channels],
              muted: false,
            };
            return (
              <EventRow
                key={desc.key}
                desc={desc}
                entry={entry}
                titleKey={desc.title_key}
                messageKey={desc.message_key}
                tNs={tNs}
                onToggleChannel={(c) => toggleChannel(desc.key, c)}
                onToggleMute={() => toggleMute(desc.key)}
              />
            );
          })}
        </CardContent>
      </Card>

      <div className="mt-3 flex justify-end">
        <Button
          onClick={onSave}
          disabled={!dirty || update.isPending}
        >
          {update.isPending ? t("loading") : t("saveButton")}
        </Button>
      </div>
    </div>
  );
}

/**
 * Convert an ISO timestamp from the API into the value shape an
 * ``<input type="datetime-local">`` expects (``YYYY-MM-DDTHH:mm``).
 * Returns an empty string when the input is null/invalid so the
 * picker shows its placeholder.
 */
function isoToLocalDatetimeInputValue(iso: string | null | undefined): string {
  if (!iso) return "";
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return "";
  const pad = (n: number) => String(n).padStart(2, "0");
  return (
    `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}` +
    `T${pad(date.getHours())}:${pad(date.getMinutes())}`
  );
}

/**
 * Reverse helper — turn the picker value back into an ISO string the
 * backend can store. Empty input → ``null`` so the API drops the
 * mute window entirely.
 */
function localDatetimeInputToIso(value: string): string | null {
  if (!value) return null;
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return null;
  return date.toISOString();
}

type NamespaceTranslator = ReturnType<typeof useTranslations>;

interface EventRowProps {
  desc: NotificationEventDescriptor;
  entry: NotificationPrefEntry;
  titleKey: string;
  messageKey: string;
  tNs: NamespaceTranslator;
  onToggleChannel: (c: ChannelKind) => void;
  onToggleMute: () => void;
}

function EventRow({
  desc,
  entry,
  titleKey,
  messageKey,
  tNs,
  onToggleChannel,
  onToggleMute,
}: EventRowProps) {
  const t = useTranslations("notification");
  const renderedTitle = safeT(tNs, titleKey, desc.key);
  const renderedMessage = safeT(tNs, messageKey, "");
  const hasInApp = entry.channels.includes("in_app");
  const hasEmail = entry.channels.includes("email");
  const emailLocked = desc.requires_email;

  return (
    <div className="rounded-md border p-3">
      <div className="mb-2 flex items-start gap-2">
        <div className="flex-1">
          <div className="flex items-center gap-2">
            <span className="text-sm font-medium">{renderedTitle}</span>
            {desc.default_urgency === "critical" && (
              <Badge variant="destructive">{t("urgencyCritical")}</Badge>
            )}
            {desc.default_urgency === "warn" && (
              <Badge variant="outline">{t("urgencyWarn")}</Badge>
            )}
          </div>
          <p className="mt-0.5 text-[11px] sh-muted">{renderedMessage}</p>
          <div className="mt-1 flex flex-wrap gap-2 text-[10px] sh-muted">
            <span>
              {t("audienceLabel")}: {desc.target_audience}
            </span>
            {desc.cooldown_seconds > 0 && (
              <span>
                {t("cooldownLabel", {
                  seconds: desc.cooldown_seconds,
                })}
              </span>
            )}
          </div>
        </div>
        <div className="flex items-center gap-2">
          <span className="text-[11px] sh-muted">{t("mutedLabel")}</span>
          <Switch checked={entry.muted} onCheckedChange={onToggleMute} />
        </div>
      </div>
      <div className="flex flex-wrap gap-2">
        <ChannelChip
          active={hasInApp && !entry.muted}
          disabled={entry.muted}
          label={t("channelInApp")}
          onClick={() => onToggleChannel("in_app")}
        />
        <ChannelChip
          active={hasEmail || emailLocked}
          disabled={emailLocked}
          label={t("channelEmail")}
          icon={emailLocked ? <IconLock className="size-3" /> : <IconMailFast className="size-3" />}
          tooltip={emailLocked ? t("requiresEmailNote") : undefined}
          onClick={() => !emailLocked && onToggleChannel("email")}
        />
      </div>
    </div>
  );
}

function ChannelChip({
  active,
  disabled,
  label,
  icon,
  tooltip,
  onClick,
}: {
  active: boolean;
  disabled?: boolean;
  label: string;
  icon?: React.ReactNode;
  tooltip?: string;
  onClick: () => void;
}) {
  const chip = (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled}
      className={cn(
        "flex items-center gap-1 rounded-full border px-2 py-1 text-[11px] transition-colors",
        active
          ? "border-[rgb(var(--color-primary))] bg-[rgb(var(--color-primary))]/10 text-[rgb(var(--color-primary))]"
          : "border-neutral-300 dark:border-neutral-700 sh-muted",
        disabled && "cursor-not-allowed opacity-70",
      )}
    >
      {icon}
      {label}
    </button>
  );
  if (!tooltip) return chip;
  return (
    <Tooltip>
      <TooltipTrigger asChild>{chip}</TooltipTrigger>
      <TooltipContent>{tooltip}</TooltipContent>
    </Tooltip>
  );
}

const PREVIEW_PLACEHOLDER = "…";

function safeT(
  tNs: NamespaceTranslator,
  key: string,
  fallback: string,
): string {
  try {
    const raw = tNs.raw(key);
    if (typeof raw !== "string" || raw === key) return fallback;
    const values = placeholderValuesFromTemplate(raw);
    const value =
      Object.keys(values).length > 0 ? tNs(key, values) : tNs(key);
    if (typeof value === "string" && value !== key) return value;
    return fallback;
  } catch {
    return fallback;
  }
}

function placeholderValuesFromTemplate(
  template: string,
): Record<string, string> {
  const values: Record<string, string> = {};
  for (const match of template.matchAll(/\{([a-zA-Z_][a-zA-Z0-9_]*)\}/g)) {
    const name = match[1];
    if (name && !(name in values)) {
      values[name] = PREVIEW_PLACEHOLDER;
    }
  }
  return values;
}
