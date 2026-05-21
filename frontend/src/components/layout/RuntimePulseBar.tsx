"use client";

import { Link } from "@/lib/navigation";
import { IconArrowRight } from "@tabler/icons-react";
import { useTranslations } from "next-intl";

import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip";
import { useAgentRuntimeSnapshot } from "@/hooks/use-agent-runtime";

export function RuntimePulseBar() {
  const t = useTranslations("sidebar.pulse");
  const { data } = useAgentRuntimeSnapshot();
  const summary = data?.summary;

  if (!summary) return null;
  const { running, stuck, orphan } = summary;
  if (running + stuck + orphan === 0) return null;

  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <Link
          href="/agent-view"
          aria-label={t("viewAll")}
          className="mx-2 ml-9 mr-3 mt-0.5 flex h-7 items-center gap-2.5 rounded-md px-2 text-[11px] tabular-nums sh-muted transition-colors hover:bg-black/5 dark:hover:bg-white/10"
        >
          <PulseStat color="emerald" value={running} label={t("running")} />
          <PulseStat color="amber" value={stuck} label={t("stuck")} />
          <PulseStat color="rose" value={orphan} label={t("orphan")} />
          <IconArrowRight className="ml-auto size-3 shrink-0" aria-hidden />
        </Link>
      </TooltipTrigger>
      <TooltipContent side="right">{t("viewAll")}</TooltipContent>
    </Tooltip>
  );
}

const COLOR_DOT: Record<"emerald" | "amber" | "rose", string> = {
  emerald: "bg-emerald-500",
  amber: "bg-amber-500",
  rose: "bg-rose-500",
};

function PulseStat({
  color,
  value,
  label,
}: {
  color: "emerald" | "amber" | "rose";
  value: number;
  label: string;
}) {
  return (
    <span className="flex items-center gap-1" title={label}>
      <span aria-hidden className={`size-1.5 rounded-full ${COLOR_DOT[color]}`} />
      <span>{value}</span>
    </span>
  );
}
