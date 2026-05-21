"use client";

import { Link } from "@/lib/navigation";
import { IconMessageCircle } from "@tabler/icons-react";
import { useLocale, useTranslations } from "next-intl";

import { useRecentSessions } from "@/hooks/use-sessions";
import { relativeTime } from "@/lib/utils";

/**
 * "Recent sessions" bento tile.
 *
 * Mirrors `ActiveAgentsList`: same H3 + "History" affordance, but the
 * rows here are link-only — chat bubble icon (mt-0.5 to align to the
 * first text line), title (group-hover → primary), and a meta line
 * with relative time + optional message count when the API exposes it.
 */
export function RecentSessionsCard() {
  const t = useTranslations("dashboard");
  const tChat = useTranslations("chat");
  const locale = useLocale();
  const { data: sessions } = useRecentSessions(8);

  const items = (sessions ?? []).slice(0, 5);

  return (
    <section className="flex h-full flex-col rounded-xl border sh-card p-4 md:p-5">
      <header className="mb-4 flex items-center justify-between border-b pb-3">
        <h2 className="text-[20px] font-semibold leading-7 tracking-tight">
          {t("recentSessionsTitle")}
        </h2>
        <Link
          href="/chat"
          className="text-[12px] font-medium text-[rgb(var(--color-primary))] hover:underline"
        >
          {t("recentSessionsHistory")}
        </Link>
      </header>

      {items.length === 0 ? (
        <p className="flex flex-1 items-center justify-center py-8 text-center text-[12px] sh-muted">
          {t("recentSessionsEmpty")}
        </p>
      ) : (
        <ul className="flex-1 space-y-3 overflow-y-auto pr-1">
          {items.map((s) => {
            const meta: string[] = [];
            if (s.last_message_at) {
              meta.push(relativeTime(s.last_message_at, locale));
            }
            const count = (s as { message_count?: number }).message_count;
            if (typeof count === "number" && count > 0) {
              meta.push(t("recentSessionsMessages", { count }));
            }

            return (
              <li key={s.id}>
                <Link
                  href={`/chat/${s.id}`}
                  className="group flex items-start gap-3 rounded-md py-1 transition-colors"
                >
                  <IconMessageCircle
                    className="mt-0.5 size-4 shrink-0 sh-muted transition-colors group-hover:text-[rgb(var(--color-primary))]"
                    aria-hidden
                  />
                  <div className="min-w-0 flex-1">
                    <div className="line-clamp-1 text-[14px] font-medium transition-colors group-hover:text-[rgb(var(--color-primary))]">
                      {s.title ?? tChat("untitled")}
                    </div>
                    {meta.length > 0 && (
                      <div className="mt-0.5 text-[12px] sh-muted tabular-nums">
                        {meta.join(" • ")}
                      </div>
                    )}
                  </div>
                </Link>
              </li>
            );
          })}
        </ul>
      )}
    </section>
  );
}
