"use client";

import { useTranslations } from "next-intl";
import {
  IconActivity,
  IconAlertTriangle,
  IconGhost2,
  IconPlayerPlay,
  IconUsers,
} from "@tabler/icons-react";

import type { RuntimeSnapshot } from "@/hooks/use-agent-runtime";
import { cn } from "@/lib/utils";

interface RuntimeSummaryBarProps {
  summary: RuntimeSnapshot["summary"] | null;
}

const STAT_ORDER = [
  { key: "running", icon: IconPlayerPlay, tone: "text-emerald-500" },
  { key: "stuck", icon: IconAlertTriangle, tone: "text-amber-500" },
  { key: "orphan", icon: IconGhost2, tone: "text-rose-500" },
  { key: "queued", icon: IconActivity, tone: "text-sky-500" },
  { key: "subagents_active", icon: IconUsers, tone: "text-purple-500" },
] as const;

export function RuntimeSummaryBar({ summary }: RuntimeSummaryBarProps) {
  const t = useTranslations("agentView.summary");
  return (
    <div className="grid grid-cols-2 gap-2 sm:grid-cols-5">
      {STAT_ORDER.map(({ key, icon: Icon, tone }) => (
        <div
          key={key}
          className="flex items-center gap-2 rounded-md border bg-card px-3 py-2"
        >
          <Icon className={cn("size-4 shrink-0", tone)} />
          <div className="min-w-0">
            <div className="text-[10px] uppercase tracking-wide sh-muted">
              {t(key)}
            </div>
            <div className="text-lg font-semibold tabular-nums">
              {summary ? summary[key] ?? 0 : 0}
            </div>
          </div>
        </div>
      ))}
    </div>
  );
}
