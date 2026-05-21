"use client";

import { useTranslations } from "next-intl";

import { Badge } from "@/components/ui/badge";
import type { SkillPackState } from "@/hooks/use-skill-lifecycle";
import { cn } from "@/lib/utils";

interface Props {
  state: SkillPackState;
  pinned?: boolean;
  className?: string;
}

const TONE: Record<SkillPackState, string> = {
  active: "bg-green-500/15 text-green-700 dark:text-green-400",
  stale: "bg-amber-500/15 text-amber-700 dark:text-amber-400",
  draft: "bg-sky-500/15 text-sky-700 dark:text-sky-400",
  candidate: "bg-violet-500/15 text-violet-700 dark:text-violet-400",
  pinned: "bg-blue-500/15 text-blue-700 dark:text-blue-400",
  archived: "bg-zinc-500/15 text-zinc-700 dark:text-zinc-300",
  superseded: "bg-fuchsia-500/15 text-fuchsia-700 dark:text-fuchsia-400",
  deprecated: "bg-orange-500/15 text-orange-700 dark:text-orange-400",
  rejected: "bg-rose-500/15 text-rose-700 dark:text-rose-400",
  tombstone: "bg-zinc-900 text-zinc-100",
};

export function SkillStateBadge({ state, pinned, className }: Props) {
  const t = useTranslations("skillLifecycle.states");
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1",
        pinned && "rounded-full ring-2 ring-blue-500/60",
        className,
      )}
    >
      <Badge className={cn(TONE[state], "px-2")}>{t(state)}</Badge>
      {pinned && state !== "pinned" && (
        <Badge className="bg-blue-500/15 text-blue-700 dark:text-blue-400">
          📌
        </Badge>
      )}
    </span>
  );
}
