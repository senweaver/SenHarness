"use client";

import { Link } from "@/lib/navigation";
import { useTranslations } from "next-intl";

import { useAgents } from "@/hooks/use-agents";
import { Skeleton } from "@/components/ui/skeleton";
import { cn } from "@/lib/utils";

const ICON_PALETTE = [
  "bg-[rgb(var(--color-primary)/0.15)] text-[rgb(var(--color-primary))]",
  "bg-amber-500/15 text-amber-600",
  "bg-emerald-500/15 text-emerald-600",
  "bg-violet-500/15 text-violet-600",
  "bg-rose-500/15 text-rose-600",
  "bg-sky-500/15 text-sky-600",
];

function pickPaletteIndex(seed: string): number {
  let h = 0;
  for (let i = 0; i < seed.length; i++) h = (h * 31 + seed.charCodeAt(i)) | 0;
  return Math.abs(h) % ICON_PALETTE.length;
}

/**
 * "Active agents" bento tile.
 *
 * Header with H3 + "View all" link, then a tight list of agent rows.
 * Each row is hover-highlightable with a transparent border that
 * fades in on hover (no border shift on rest), 10×10 colored avatar
 * tile, and a status chip on the right.
 */
export function ActiveAgentsList() {
  const t = useTranslations("dashboard");
  const { data: agents, isLoading } = useAgents();

  const items = (agents ?? []).slice(0, 5);

  return (
    <section className="flex h-full flex-col rounded-xl border sh-card p-4 md:p-5">
      <header className="mb-4 flex items-center justify-between border-b pb-3">
        <h2 className="text-[20px] font-semibold leading-7 tracking-tight">
          {t("activeAgentsTitle")}
        </h2>
        <Link
          href="/agents"
          className="text-[12px] font-medium text-[rgb(var(--color-primary))] hover:underline"
        >
          {t("activeAgentsManage")}
        </Link>
      </header>

      {isLoading ? (
        <div className="space-y-2">
          <Skeleton className="h-14" />
          <Skeleton className="h-14" />
          <Skeleton className="h-14" />
        </div>
      ) : items.length === 0 ? (
        <p className="flex flex-1 items-center justify-center py-8 text-center text-[12px] sh-muted">
          {t("activeAgentsEmpty")}
        </p>
      ) : (
        <ul className="flex-1 space-y-1 overflow-y-auto pr-1">
          {items.map((agent) => {
            const palette = ICON_PALETTE[pickPaletteIndex(agent.id)];
            const status = pickStatus(agent.autonomy_level);
            return (
              <li key={agent.id}>
                <Link
                  href={`/agents/${agent.id}`}
                  className="flex items-center justify-between rounded-lg border border-transparent p-2 transition-colors hover:border-[rgb(var(--color-border))] hover:bg-black/5 dark:hover:bg-white/5"
                >
                  <div className="flex min-w-0 items-center gap-3">
                    {agent.avatar_url ? (
                      // eslint-disable-next-line @next/next/no-img-element
                      <img
                        src={agent.avatar_url}
                        alt=""
                        className="size-10 rounded-lg object-cover"
                      />
                    ) : (
                      <div
                        className={cn(
                          "flex size-10 items-center justify-center rounded-lg text-[15px] font-semibold",
                          palette,
                        )}
                      >
                        {agent.name.trim().charAt(0).toUpperCase() || "?"}
                      </div>
                    )}
                    <div className="min-w-0">
                      <div className="truncate text-[14px] font-medium">
                        {agent.name}
                      </div>
                      <div className="truncate text-[12px] sh-muted">
                        {agent.description ?? `Model: ${agent.backend_kind}`}
                      </div>
                    </div>
                  </div>
                  <StatusChip kind={status} label={t(`status.${status}`)} />
                </Link>
              </li>
            );
          })}
        </ul>
      )}
    </section>
  );
}

type AgentStatus = "working" | "idle" | "ready";

function pickStatus(autonomy: string): AgentStatus {
  // Cheap mapping until backend exposes runtime status — keeps the UI
  // varied (and tests the visual states) without inventing fake data.
  const lower = autonomy.toLowerCase();
  if (lower === "auto" || lower === "high") return "working";
  if (lower === "supervised" || lower === "medium") return "ready";
  return "idle";
}

function StatusChip({ kind, label }: { kind: AgentStatus; label: string }) {
  if (kind === "working") {
    return (
      <span className="inline-flex shrink-0 items-center gap-1.5 rounded-full px-2 py-0.5 text-[11px] font-semibold text-[rgb(var(--color-primary))]">
        <span
          className="size-1.5 animate-pulse rounded-full bg-[rgb(var(--color-primary))]"
          aria-hidden
        />
        {label}
      </span>
    );
  }
  if (kind === "ready") {
    return (
      <span className="inline-flex shrink-0 items-center rounded-full bg-emerald-500/12 px-2 py-0.5 text-[11px] font-semibold text-emerald-600">
        {label}
      </span>
    );
  }
  return (
    <span className="inline-flex shrink-0 items-center rounded-full bg-black/5 px-2 py-0.5 text-[11px] font-semibold sh-muted dark:bg-white/10">
      {label}
    </span>
  );
}
