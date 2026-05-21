"use client";

import { useMemo, useState } from "react";
import { Link } from "@/lib/navigation";
import {
  IconClock,
  IconLoader2,
  IconPlus,
} from "@tabler/icons-react";
import { useLocale, useTranslations } from "next-intl";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { useFlows } from "@/hooks/use-flows";
import { AddScheduleDialog } from "@/components/agents/dialogs/AddScheduleDialog";
import { relativeTime } from "@/lib/utils";

export function SchedulesTab({ agentId }: { agentId: string }) {
  const t = useTranslations("agentDetail.schedules");
  const tFlow = useTranslations("flows");
  const locale = useLocale();
  const { data: flows, isLoading } = useFlows();
  const [open, setOpen] = useState(false);

  const items = useMemo(
    () => (flows ?? []).filter((f) => f.agent_id === agentId),
    [flows, agentId],
  );

  return (
    <div className="space-y-4">
      <header className="flex items-center justify-between">
        <h2 className="text-base font-semibold">{t("title")}</h2>
        <Button size="sm" onClick={() => setOpen(true)}>
          <IconPlus className="size-4" />
          {t("addCta")}
        </Button>
      </header>

      {isLoading ? (
        <div className="flex items-center gap-2 rounded-md border p-4 text-[13px] sh-muted">
          <IconLoader2 className="size-4 animate-spin" />…
        </div>
      ) : items.length === 0 ? (
        <p className="rounded-md border border-dashed p-8 text-center text-[13px] sh-muted">
          {t("empty")}
        </p>
      ) : (
        <ul className="rounded-md border sh-card divide-y">
          {items.map((flow) => (
            <li key={flow.id}>
              <Link
                href={`/flows/${flow.id}`}
                className="flex items-center gap-3 px-4 py-3 text-sm transition-colors hover:bg-black/5 dark:hover:bg-white/5"
              >
                <IconClock className="size-4 shrink-0 sh-muted" />
                <div className="min-w-0 flex-1">
                  <div className="flex items-center gap-2">
                    <span className="truncate font-medium">{flow.name}</span>
                    {!flow.enabled && (
                      <Badge variant="outline">
                        {tFlow("disabled")}
                      </Badge>
                    )}
                  </div>
                  <div className="mt-0.5 flex items-center gap-2 text-[11px] sh-muted">
                    <span className="rounded bg-black/5 px-1.5 font-mono text-[10px] dark:bg-white/10">
                      {tFlow(`trigger.${flow.trigger_kind}`)}
                    </span>
                    {flow.last_run_at ? (
                      <span>
                        {tFlow("lastRun", {
                          when: relativeTime(flow.last_run_at, locale),
                        })}
                      </span>
                    ) : (
                      <span>{tFlow("neverRun")}</span>
                    )}
                  </div>
                </div>
              </Link>
            </li>
          ))}
        </ul>
      )}

      <AddScheduleDialog open={open} onOpenChange={setOpen} agentId={agentId} />
    </div>
  );
}
