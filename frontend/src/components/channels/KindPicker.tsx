"use client";

import { useMemo } from "react";
import { useTranslations } from "next-intl";

import { Skeleton } from "@/components/ui/skeleton";
import {
  type ChannelKind,
  type ChannelKindMeta,
  useChannelKinds,
} from "@/hooks/use-channels";
import {
  CHANNEL_PROVIDERS,
  getChannelProvider,
} from "@/lib/channel-providers";
import { cn } from "@/lib/utils";

interface KindPickerProps {
  onPick: (kind: ChannelKind) => void;
  className?: string;
}

export function KindPicker({ onPick, className }: KindPickerProps) {
  const t = useTranslations("settings.channels");
  const { data: kinds, isLoading } = useChannelKinds();

  const ordered = useMemo<ChannelKindMeta[]>(() => {
    if (!kinds) return [];
    const byKind = new Map(kinds.map((k) => [k.kind, k]));
    const front = CHANNEL_PROVIDERS.map((p) => byKind.get(p.kind)).filter(
      (k): k is ChannelKindMeta => Boolean(k),
    );
    const known = new Set(front.map((k) => k.kind));
    const tail = kinds.filter((k) => !known.has(k.kind));
    return [...front, ...tail];
  }, [kinds]);

  if (isLoading) return <Skeleton className="h-40" />;

  return (
    <div className={cn("space-y-3", className)}>
      <h3 className="text-sm font-medium">{t("pickKind")}</h3>
      <div className="grid grid-cols-1 gap-2 sm:grid-cols-2 lg:grid-cols-3">
        {ordered.map((kind) => (
          <KindTile key={kind.kind} meta={kind} onPick={onPick} />
        ))}
      </div>
    </div>
  );
}

function KindTile({
  meta,
  onPick,
}: {
  meta: ChannelKindMeta;
  onPick: (k: ChannelKind) => void;
}) {
  const t = useTranslations("settings.channels");
  const brand = getChannelProvider(meta.kind);
  const Icon = brand.icon;
  return (
    <button
      type="button"
      onClick={() => onPick(meta.kind)}
      className="group flex items-start gap-3 rounded-md border border-[rgb(var(--color-border))] bg-[rgb(var(--color-card))] p-3 text-left transition hover:border-[rgb(var(--color-primary))] hover:shadow-sm"
    >
      <span
        className={cn(
          "flex size-9 shrink-0 items-center justify-center rounded-md",
          brand.iconBg,
          brand.iconFg,
        )}
        aria-hidden
      >
        <Icon size={20} />
      </span>
      <span className="flex min-w-0 flex-1 flex-col">
        <span className="truncate text-sm font-medium">
          {t.has(brand.nameKey)
            ? t(brand.nameKey.replace("settings.channels.", ""))
            : meta.display_name}
        </span>
        <span className="truncate text-[11px] sh-muted">
          {t.has(brand.subtitleKey)
            ? t(brand.subtitleKey.replace("settings.channels.", ""))
            : meta.description.slice(0, 80)}
        </span>
      </span>
    </button>
  );
}
