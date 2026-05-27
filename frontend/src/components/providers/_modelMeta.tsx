"use client";

import { Button } from "@/components/ui/button";
import {
  IconArrowsSort,
  IconBrain,
  IconCode,
  IconEye,
  IconMessageCircle,
  IconMicrophone,
  IconMusic,
  IconPencil,
  IconPhoto,
  IconStarFilled,
  IconTool,
  IconTrash,
  IconVector,
  IconVideo,
  IconVolume,
  IconWorld,
} from "@tabler/icons-react";
import { useTranslations } from "next-intl";
import { Badge } from "@/components/ui/badge";
import { Switch } from "@/components/ui/switch";
import { SimpleTooltip } from "@/components/ui/tooltip";
import type { ModelCategory, ProviderModelRead } from "@/hooks/use-providers";
import { cn } from "@/lib/utils";

export interface ModelLike {
  model: string;
  metadata_json?: Record<string, unknown>;
  category?: string | null | undefined;
  family?: string | null | undefined;
  capabilities?: string[];
}

export function getCategory(m: ModelLike): ModelCategory {
  if (m.category) return m.category as ModelCategory;
  if (m.metadata_json) {
    const c = m.metadata_json.category;
    if (typeof c === "string") return c as ModelCategory;
  }
  const id = m.model.toLowerCase();
  if (/embed|rerank|voyage/.test(id)) return "embedding";
  if (/whisper|stt|transcribe|asr/.test(id)) return "asr";
  if (/tts|speech|voice/.test(id)) return "tts";
  if (/dall-e|image|flux|midjourney|imagen|stable-diffusion/.test(id))
    return "image";
  if (/sora|veo|runway|video/.test(id)) return "video";
  return "chat";
}

export function getCapabilities(m: ProviderModelRead): string[] {
  const v = m.metadata_json?.capabilities;
  return Array.isArray(v) ? (v as string[]) : [];
}

export function getPricing(m: ProviderModelRead): [number, number] | null {
  const v = m.metadata_json?.pricing;
  if (Array.isArray(v) && v.length === 2 && typeof v[0] === "number") {
    return [v[0] as number, v[1] as number];
  }
  return null;
}

export function inferCapabilities(m: ModelLike): string[] {
  const explicit =
    "metadata_json" in m && m.metadata_json
      ? getCapabilities(m as ProviderModelRead)
      : Array.isArray(m.capabilities)
        ? m.capabilities
        : [];
  const set = new Set(explicit.map((c) => c.toLowerCase()));
  const id = m.model.toLowerCase();
  const family = (m.family ?? "").toLowerCase();
  const cat = getCategory(m);

  if (cat === "chat") set.add("chat");
  if (cat === "image") set.add("image");
  if (cat === "video") set.add("video");
  if (cat === "embedding") set.add("embedding");
  if (cat === "asr") set.add("asr");
  if (cat === "tts") set.add("tts");

  // Explicit ``metadata_json.profile.reasoning.supported = true`` wins
  // over regex sniffing so an operator-tagged thinking model gets the
  // badge regardless of its naming pattern.
  const reasoningProfile =
    "metadata_json" in m && m.metadata_json
      ? ((m.metadata_json as { profile?: { reasoning?: { supported?: boolean } } })
          ?.profile?.reasoning?.supported ?? null)
      : null;
  if (reasoningProfile === true) {
    set.add("reasoning");
  } else if (
    family.includes("reasoning") ||
    /\b(o\d|reasoner|thinking|r1)\b/.test(id)
  ) {
    set.add("reasoning");
  }
  if (/\b(vision|vl|-v|gpt-4o|claude-3|gemini)\b/.test(id) && cat === "chat")
    set.add("vision");
  if (/\b(tools|function|tool-use)\b/.test(id) || cat === "chat")
    set.add("tools");
  if (/coder|coding|code/.test(id) || family.includes("coding"))
    set.add("coding");
  if (/search|browse|online|web/.test(id)) set.add("web");
  if (/rerank/.test(id)) set.add("rerank");

  return Array.from(set);
}

export const CAPABILITY_META: Record<
  string,
  { icon: typeof IconBrain; tooltipKey: string }
> = {
  reasoning: { icon: IconBrain, tooltipKey: "reasoning" },
  vision: { icon: IconEye, tooltipKey: "vision" },
  tools: { icon: IconTool, tooltipKey: "tools" },
  web: { icon: IconWorld, tooltipKey: "web" },
  embedding: { icon: IconVector, tooltipKey: "embedding" },
  rerank: { icon: IconArrowsSort, tooltipKey: "rerank" },
  image: { icon: IconPhoto, tooltipKey: "image" },
  video: { icon: IconVideo, tooltipKey: "video" },
  chat: { icon: IconMessageCircle, tooltipKey: "chat" },
  audio: { icon: IconMusic, tooltipKey: "audio" },
  tts: { icon: IconVolume, tooltipKey: "tts" },
  asr: { icon: IconMicrophone, tooltipKey: "asr" },
  coding: { icon: IconCode, tooltipKey: "coding" },
};

export const CAP_DISPLAY_ORDER = [
  "reasoning",
  "vision",
  "tools",
  "web",
  "coding",
  "embedding",
  "rerank",
  "image",
  "video",
  "tts",
  "asr",
  "audio",
];

export function ModelCapabilityIcons({
  capabilities,
  className,
}: {
  capabilities: string[];
  className?: string;
}) {
  const t = useTranslations("settings.providers.models.capabilities");
  if (capabilities.length === 0) return null;
  const ordered = CAP_DISPLAY_ORDER.filter((c) => capabilities.includes(c));
  if (ordered.length === 0) return null;
  return (
    <span className={cn("inline-flex items-center gap-1", className)}>
      {ordered.map((cap) => {
        const meta = CAPABILITY_META[cap];
        if (!meta) return null;
        const Icon = meta.icon;
        return (
          <SimpleTooltip key={cap} label={t(meta.tooltipKey)}>
            <span className="inline-flex">
              <Icon className="size-3 text-muted-foreground hover:text-foreground transition" />
            </span>
          </SimpleTooltip>
        );
      })}
    </span>
  );
}

export function formatPrice(p: number): string {
  if (p === 0) return "$0";
  if (p < 0.1) return `$${p.toFixed(3)}`;
  return `$${p.toFixed(2)}`;
}

export function formatCtx(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(0)}M`;
  if (n >= 1_000) return `${(n / 1_000).toFixed(0)}K`;
  return `${n}`;
}

const CATEGORY_TABS: Array<{
  id: "all" | ModelCategory;
  icon: typeof IconMessageCircle | null;
}> = [
  { id: "all", icon: null },
  { id: "chat", icon: IconMessageCircle },
  { id: "image", icon: IconPhoto },
  { id: "video", icon: IconVideo },
  { id: "embedding", icon: IconVector },
  { id: "asr", icon: IconMicrophone },
  { id: "tts", icon: IconVolume },
];

export function CategoryTabs({
  active,
  counts,
  onChange,
}: {
  active: "all" | ModelCategory;
  counts: Record<"all" | ModelCategory, number>;
  onChange: (id: "all" | ModelCategory) => void;
}) {
  const t = useTranslations("settings.providers.models");
  return (
    <div className="border-b">
      <div className="flex flex-wrap items-center gap-1">
        {CATEGORY_TABS.map((c) => {
          const count = counts[c.id];
          if (c.id !== "all" && count === 0) return null;
          const Icon = c.icon;
          return (
            <button
              key={c.id}
              type="button"
              onClick={() => onChange(c.id)}
              className={cn(
                "flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium border-b-2 -mb-px transition",
                active === c.id
                  ? "border-primary text-foreground"
                  : "border-transparent text-muted-foreground hover:text-foreground",
              )}
            >
              {Icon ? <Icon className="size-3.5" /> : null}
              {t(`categories.${c.id}`)}
              {count > 0 ? (
                <span className="text-[10px] opacity-70">({count})</span>
              ) : null}
            </button>
          );
        })}
      </div>
    </div>
  );
}

export function ModelToggleRow({
  model,
  label,
  family,
  category,
  capabilities,
  contextWindow,
  recommended,
  enabled,
  onToggle,
  onEdit,
  onDelete,
  canDelete,
  dragHandle,
  disabledHint,
  pending,
  className,
}: {
  model: string;
  label?: string | null;
  family?: string | null;
  category?: ModelCategory;
  capabilities?: string[];
  contextWindow?: number | null;
  recommended?: boolean;
  enabled: boolean;
  onToggle?: (next: boolean) => void;
  onEdit?: () => void;
  onDelete?: () => void;
  canDelete?: boolean;
  dragHandle?: React.ReactNode;
  disabledHint?: string;
  pending?: boolean;
  className?: string;
}) {
  const t = useTranslations("settings.providers.models");
  const caps = inferCapabilities({ model, family, category, capabilities });
  const isReadOnly = !onToggle;

  const switchEl = (
    <Switch
      checked={enabled}
      disabled={isReadOnly || pending}
      onCheckedChange={(v) => onToggle?.(v)}
      aria-label={enabled ? t("disable") : t("enable")}
    />
  );

  return (
    <li
      className={cn(
        "flex items-center gap-2 rounded-md border bg-card px-2.5 py-1.5 transition",
        enabled ? "border-primary/30 bg-primary/5" : "",
        className,
      )}
    >
      {dragHandle ? <span className="flex-shrink-0">{dragHandle}</span> : null}
      <span className="w-3 flex-shrink-0 inline-flex justify-center">
        {recommended ? (
          <IconStarFilled className="size-3 text-amber-500" />
        ) : null}
      </span>
      <span className="font-mono text-xs truncate flex-1 min-w-0">
        {model}
      </span>
      {label && label !== model ? (
        <span className="text-[11px] text-muted-foreground truncate max-w-[10rem] hidden md:inline">
          {label}
        </span>
      ) : null}
      {family ? (
        <Badge variant="outline" className="text-[10px] hidden lg:inline-flex">
          {family}
        </Badge>
      ) : null}
      <ModelCapabilityIcons capabilities={caps} />
      {contextWindow ? (
        <span className="text-[11px] text-muted-foreground tabular-nums hidden sm:inline">
          {formatCtx(contextWindow)}
        </span>
      ) : null}
      {onEdit ? (
        <SimpleTooltip label={t("edit")}>
          <Button
            type="button"
            variant="ghost"
            size="icon"
            className="size-7 text-muted-foreground hover:text-foreground"
            onClick={onEdit}
            disabled={pending}
          >
            <IconPencil className="size-3.5" />
          </Button>
        </SimpleTooltip>
      ) : null}
      {onDelete && canDelete ? (
        <SimpleTooltip label={t("delete")}>
          <Button
            type="button"
            variant="ghost"
            size="icon"
            className="size-7 text-muted-foreground hover:text-destructive"
            onClick={onDelete}
            disabled={pending}
          >
            <IconTrash className="size-3.5" />
          </Button>
        </SimpleTooltip>
      ) : null}
      {isReadOnly && disabledHint ? (
        <SimpleTooltip label={disabledHint}>
          <span className="inline-flex">{switchEl}</span>
        </SimpleTooltip>
      ) : (
        switchEl
      )}
    </li>
  );
}
