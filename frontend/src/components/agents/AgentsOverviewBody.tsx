"use client";

import { useMemo, useState } from "react";
import { useTranslations } from "next-intl";
import { IconRefresh, IconLoader2 } from "@tabler/icons-react";
import { toast } from "sonner";
import { useQueryClient } from "@tanstack/react-query";

import { Button } from "@/components/ui/button";
import { RuntimeCard } from "@/components/agent-view/RuntimeCard";
import { RuntimeDetailDrawer } from "@/components/agent-view/RuntimeDetailDrawer";
import {
  RuntimeFilterChips,
  type RuntimeFilter,
} from "@/components/agent-view/RuntimeFilterChips";
import { RuntimeSummaryBar } from "@/components/agent-view/RuntimeSummaryBar";
import {
  useAgentRuntimeSnapshot,
  useAgentRuntimeStream,
  type RuntimeRunCard,
} from "@/hooks/use-agent-runtime";
import { api } from "@/lib/api";

function classifyCard(card: RuntimeRunCard): RuntimeFilter {
  if (card.stuck_reason) return "stuck";
  if (card.orphan) return "orphan";
  if (card.running_tool_name) return "thinking";
  return "healthy";
}

export function AgentsOverviewBody() {
  const t = useTranslations("agentView");
  const snapshotQ = useAgentRuntimeSnapshot();
  useAgentRuntimeStream();
  const qc = useQueryClient();

  const [filter, setFilter] = useState<RuntimeFilter>("all");
  const [activeRunId, setActiveRunId] = useState<string | null>(null);
  const [sweeping, setSweeping] = useState(false);

  const cards = snapshotQ.data?.runs ?? [];

  const counts = useMemo<Record<RuntimeFilter, number>>(() => {
    const acc: Record<RuntimeFilter, number> = {
      all: cards.length,
      healthy: 0,
      thinking: 0,
      stuck: 0,
      orphan: 0,
    };
    for (const card of cards) {
      acc[classifyCard(card)] += 1;
    }
    return acc;
  }, [cards]);

  const filtered = useMemo(() => {
    if (filter === "all") return cards;
    return cards.filter((c) => classifyCard(c) === filter);
  }, [cards, filter]);

  const activeCard = useMemo(
    () => cards.find((c) => c.run_id === activeRunId) ?? null,
    [cards, activeRunId],
  );

  const onSweep = async () => {
    setSweeping(true);
    try {
      const result = await api.post<{
        stale_seen: number;
        reaped: number;
      }>(`/api/v1/agent-runtime/sweep`, {});
      toast.success(
        t("sweep.done", { reaped: result.reaped, seen: result.stale_seen }),
      );
      qc.invalidateQueries({ queryKey: ["agent-runtime", "snapshot"] });
    } catch (err) {
      toast.error(t("sweep.failed", { error: (err as Error).message }));
    } finally {
      setSweeping(false);
    }
  };

  return (
    <div className="flex flex-col gap-4">
      <header className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h2 className="text-lg font-semibold">{t("title")}</h2>
          <p className="text-xs sh-muted">{t("subtitle")}</p>
        </div>
        <div className="flex gap-2">
          <Button
            type="button"
            variant="ghost"
            size="sm"
            onClick={() =>
              qc.invalidateQueries({ queryKey: ["agent-runtime", "snapshot"] })
            }
          >
            <IconRefresh className="mr-1 size-3.5" />
            {t("refresh")}
          </Button>
          <Button
            type="button"
            variant="outline"
            size="sm"
            onClick={onSweep}
            disabled={sweeping}
          >
            {sweeping ? (
              <IconLoader2 className="mr-1 size-3.5 animate-spin" />
            ) : null}
            {t("sweep.cta")}
          </Button>
        </div>
      </header>

      <RuntimeSummaryBar summary={snapshotQ.data?.summary ?? null} />

      <RuntimeFilterChips
        value={filter}
        counts={counts}
        onChange={setFilter}
      />

      {snapshotQ.isLoading ? (
        <div className="flex flex-1 items-center justify-center sh-muted">
          <IconLoader2 className="mr-2 size-4 animate-spin" />
          {t("loading")}
        </div>
      ) : filtered.length === 0 ? (
        <div className="flex flex-1 items-center justify-center sh-muted">
          {t("empty")}
        </div>
      ) : (
        <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
          {filtered.map((card) => (
            <RuntimeCard
              key={card.run_id}
              card={card}
              onClick={() => setActiveRunId(card.run_id)}
            />
          ))}
        </div>
      )}

      <RuntimeDetailDrawer
        card={activeCard}
        open={activeRunId !== null}
        onOpenChange={(o) => {
          if (!o) setActiveRunId(null);
        }}
      />
    </div>
  );
}
