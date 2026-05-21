"use client";

import { useMemo, useState } from "react";
import { useTranslations } from "next-intl";
import { IconInfoCircle } from "@tabler/icons-react";
import {
  CategoryTabs,
  ModelToggleRow,
  getCategory,
} from "@/components/providers/_modelMeta";
import type {
  ModelCategory,
  ProviderCatalogEntry,
} from "@/hooks/use-providers";

export function BuiltinModelsPreview({
  entry,
}: {
  entry: ProviderCatalogEntry;
}) {
  const tModels = useTranslations("settings.providers.models");
  const tPreview = useTranslations("settings.providers.preview");
  const [tab, setTab] = useState<"all" | ModelCategory>("all");

  const builtins = useMemo(() => entry.builtin_models ?? [], [entry]);

  const categoryCounts = useMemo(() => {
    const counts: Record<"all" | ModelCategory, number> = {
      all: 0,
      chat: 0,
      image: 0,
      video: 0,
      embedding: 0,
      asr: 0,
      tts: 0,
    };
    for (const m of builtins) {
      counts[getCategory(m)]++;
      counts.all++;
    }
    return counts;
  }, [builtins]);

  const filtered = useMemo(() => {
    const rows =
      tab === "all" ? builtins : builtins.filter((m) => getCategory(m) === tab);
    return [...rows].sort((a, b) => {
      if (a.recommended !== b.recommended) return a.recommended ? -1 : 1;
      return a.model.localeCompare(b.model);
    });
  }, [builtins, tab]);

  if (builtins.length === 0) {
    return (
      <div className="space-y-3">
        <Banner message={tPreview("banner")} />
        <div className="rounded-md border border-dashed bg-muted/30 p-6 text-center text-sm text-muted-foreground">
          {tPreview("noBuiltins")}
        </div>
      </div>
    );
  }

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center gap-2">
        <h3 className="text-base font-semibold">{tModels("heading")}</h3>
        <span className="text-xs text-muted-foreground">
          {tPreview("totalAvailable", { count: builtins.length })}
        </span>
      </div>

      <Banner message={tPreview("banner")} />

      <CategoryTabs active={tab} counts={categoryCounts} onChange={setTab} />

      <ul className="flex flex-col gap-1">
        {filtered.map((m) => (
          <ModelToggleRow
            key={m.model}
            model={m.model}
            label={m.name}
            family={m.family}
            category={m.category}
            capabilities={m.capabilities}
            contextWindow={m.context_window ?? null}
            recommended={m.recommended}
            enabled={false}
            disabledHint={tPreview("addDisabledHint")}
          />
        ))}
      </ul>
    </div>
  );
}

function Banner({ message }: { message: string }) {
  return (
    <div className="flex items-start gap-2 rounded-md border border-amber-500/30 bg-amber-500/10 px-3 py-2 text-xs text-amber-900 dark:text-amber-200">
      <IconInfoCircle className="size-3.5 shrink-0 mt-0.5" />
      <span className="leading-relaxed">{message}</span>
    </div>
  );
}
