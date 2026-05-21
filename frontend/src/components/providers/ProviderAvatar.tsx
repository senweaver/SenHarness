"use client";

import { cn } from "@/lib/utils";
import type { ProviderFamily } from "@/hooks/use-providers";

const FAMILY_BG: Record<ProviderFamily | "default", string> = {
  "openai-compatible": "bg-sky-500/15 text-sky-700 dark:text-sky-300",
  anthropic: "bg-orange-500/15 text-orange-700 dark:text-orange-300",
  google: "bg-emerald-500/15 text-emerald-700 dark:text-emerald-300",
  bedrock: "bg-amber-500/15 text-amber-700 dark:text-amber-300",
  cohere: "bg-pink-500/15 text-pink-700 dark:text-pink-300",
  mistral: "bg-purple-500/15 text-purple-700 dark:text-purple-300",
  huggingface: "bg-yellow-500/15 text-yellow-700 dark:text-yellow-300",
  outlines: "bg-slate-500/15 text-slate-700 dark:text-slate-300",
  embedding: "bg-teal-500/15 text-teal-700 dark:text-teal-300",
  default: "bg-muted text-muted-foreground",
};

const SIZE: Record<"sm" | "md" | "lg" | "xl", string> = {
  sm: "size-7 text-xs",
  md: "size-9 text-sm",
  lg: "size-12 text-base",
  xl: "size-16 text-xl",
};

export function ProviderAvatar({
  displayName,
  family,
  size = "md",
  className,
}: {
  displayName: string;
  family?: ProviderFamily | string | null;
  size?: keyof typeof SIZE;
  className?: string;
}) {
  const initials = pickInitials(displayName);
  const familyKey =
    family && family in FAMILY_BG
      ? (family as keyof typeof FAMILY_BG)
      : "default";
  return (
    <span
      role="img"
      aria-label={displayName}
      className={cn(
        "inline-flex items-center justify-center rounded-md font-semibold uppercase tracking-tight select-none",
        SIZE[size],
        FAMILY_BG[familyKey],
        className,
      )}
    >
      {initials}
    </span>
  );
}

function pickInitials(name: string): string {
  if (!name) return "?";
  // Strip common parenthesized suffixes like "(OpenRouter)" before splitting.
  const cleaned = name.replace(/\(.*?\)/g, "").trim() || name;
  const parts = cleaned
    .split(/[\s\-_/·]+/)
    .filter(Boolean)
    .slice(0, 2);
  if (parts.length === 0) return cleaned.slice(0, 2).toUpperCase();
  const first = parts[0] ?? "";
  if (parts.length === 1) return first.slice(0, 2).toUpperCase();
  const second = parts[1] ?? "";
  return ((first[0] ?? "") + (second[0] ?? "")).toUpperCase();
}
