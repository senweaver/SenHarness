"use client";

import { IconRobot } from "@tabler/icons-react";
import { useTranslations } from "next-intl";

import type { RuntimeRunCard } from "@/hooks/use-agent-runtime";
import { cn } from "@/lib/utils";

interface RuntimeCardProps {
  card: RuntimeRunCard;
  onClick?: () => void;
}

function ringClassFor(card: RuntimeRunCard): string {
  if (card.stuck_reason) {
    return "ring-2 ring-amber-400 animate-[pulse_2.4s_ease-in-out_infinite]";
  }
  if (card.orphan) {
    return "ring-2 ring-rose-400 animate-[pulse_3s_ease-in-out_infinite]";
  }
  if (card.running_tool_name || card.current_phase === "executing_tool") {
    return "ring-2 ring-sky-400 animate-[pulse_2s_ease-in-out_infinite]";
  }
  return "ring-1 ring-emerald-400/70";
}

function formatDuration(ms: number): string {
  if (ms < 1000) return `${ms}ms`;
  const s = Math.floor(ms / 1000);
  if (s < 60) return `${s}s`;
  const m = Math.floor(s / 60);
  const rs = s % 60;
  if (m < 60) return `${m}m${rs.toString().padStart(2, "0")}s`;
  const h = Math.floor(m / 60);
  const rm = m % 60;
  return `${h}h${rm.toString().padStart(2, "0")}m`;
}

export function RuntimeCard({ card, onClick }: RuntimeCardProps) {
  const t = useTranslations("agentView.card");
  const tPhase = useTranslations("agentView.phase");
  const tStuck = useTranslations("agentView.stuckReason");

  const phaseLabel = card.current_phase
    ? tPhase(card.current_phase)
    : null;

  return (
    <button
      type="button"
      onClick={onClick}
      className={cn(
        "flex w-full flex-col gap-2 rounded-lg border bg-card p-4 text-left transition hover:bg-muted",
        ringClassFor(card),
      )}
    >
      <header className="flex items-start gap-3">
        {card.agent_avatar_url ? (
          // eslint-disable-next-line @next/next/no-img-element
          <img
            src={card.agent_avatar_url}
            alt=""
            className="size-10 shrink-0 rounded-md object-cover"
          />
        ) : (
          <div className="flex size-10 shrink-0 items-center justify-center rounded-md bg-muted">
            <IconRobot className="size-5 sh-muted" />
          </div>
        )}
        <div className="min-w-0 flex-1">
          <div className="truncate text-sm font-medium">
            {card.agent_name ?? t("untitledAgent")}
          </div>
          <div className="truncate text-[11px] sh-muted">
            {card.user_name ?? t("unknownUser")}
          </div>
        </div>
        <span className="rounded-sm bg-muted px-1.5 py-0.5 text-[10px] tabular-nums sh-muted">
          {formatDuration(card.age_ms)}
        </span>
      </header>

      <div className="flex flex-wrap items-center gap-1.5 text-[11px]">
        {phaseLabel ? (
          <span className="rounded-sm border bg-card px-1.5 py-0.5">
            {phaseLabel}
          </span>
        ) : null}
        {card.running_tool_name ? (
          <span className="rounded-sm border border-sky-400/40 bg-sky-50 px-1.5 py-0.5 font-mono text-[10px] text-sky-700 dark:bg-sky-950/40 dark:text-sky-300">
            {card.running_tool_name}
          </span>
        ) : null}
        {card.subagent_count > 0 ? (
          <span className="rounded-sm border px-1.5 py-0.5">
            {t("subagents", { count: card.subagent_count })}
          </span>
        ) : null}
      </div>

      {card.stuck_reason ? (
        <p className="text-[11px] text-amber-600 dark:text-amber-400">
          {tStuck(card.stuck_reason)}
        </p>
      ) : card.orphan ? (
        <p className="text-[11px] text-rose-600 dark:text-rose-400">
          {t("orphanHint")}
        </p>
      ) : null}
    </button>
  );
}
