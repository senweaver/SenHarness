"use client";

import { Link } from "@/lib/navigation";
import { IconArrowRight } from "@tabler/icons-react";
import { useLocale, useTranslations } from "next-intl";

import { AgentProfileCard } from "@/components/agents/AgentProfileCard";
import { useRecentSessions } from "@/hooks/use-sessions";
import { relativeTime } from "@/lib/utils";
import { useWorkspaceStore } from "@/stores/workspace-store";

interface RunsTabProps {
  agentId: string;
}

export function RunsTab({ agentId }: RunsTabProps) {
  const t = useTranslations("agentDetail.runs");
  const locale = useLocale();
  const { data: sessions } = useRecentSessions(50);
  const activeWorkspace = useWorkspaceStore((s) =>
    s.workspaces.find((w) => w.id === s.activeWorkspaceId),
  );
  const isAdmin =
    activeWorkspace?.role === "owner" || activeWorkspace?.role === "admin";

  const runs = (sessions ?? [])
    .filter((s) => s.subject_id === agentId)
    .slice(0, 12);

  return (
    <div className="space-y-6">
      <AgentProfileCard agentId={agentId} isAdmin={isAdmin} />

      <section className="space-y-4">
        <header>
          <h2 className="text-base font-semibold">{t("title")}</h2>
        </header>
        {runs.length === 0 ? (
          <p className="rounded-md border border-dashed p-8 text-center text-[13px] sh-muted">
            {t("empty")}
          </p>
        ) : (
          <ul className="rounded-md border sh-card divide-y">
            {runs.map((s) => (
              <li key={s.id}>
                <Link
                  href={`/traces/${s.id}`}
                  className="flex items-center gap-3 px-4 py-2.5 text-sm hover:bg-black/5 dark:hover:bg-white/5"
                >
                  <div className="min-w-0 flex-1">
                    <div className="truncate font-medium">
                      {s.title ?? s.id.slice(0, 8)}
                    </div>
                    <div className="text-[11px] sh-muted">
                      {s.message_count} messages ·{" "}
                      {s.last_message_at &&
                        relativeTime(s.last_message_at, locale)}
                    </div>
                  </div>
                  <IconArrowRight className="size-4 sh-muted" />
                </Link>
              </li>
            ))}
          </ul>
        )}
      </section>
    </div>
  );
}
