"use client";

import { useMemo } from "react";
import { useTranslations } from "next-intl";
import { IconChevronDown, IconRobot, IconUsersGroup } from "@tabler/icons-react";

import { Link, useRouter } from "@/lib/navigation";
import { useAgents } from "@/hooks/use-agents";
import { useSession } from "@/hooks/use-sessions";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { cn } from "@/lib/utils";

/**
 * Chat header for `[sessionId]/page.tsx`.
 *
 * Shows which agent (or squad) the current conversation is bound to and
 * lets the user fork a fresh chat with a different agent. Per UX choice
 * we don't mutate the active session's ``subject_id`` in place; switching
 * always navigates to ``/chat/new?agent=<id>`` so backend ``SessionUpdate``
 * stays tight (only ``title`` / ``state`` editable).
 *
 * Squad sessions render a non-interactive label — switching is meaningless
 * mid-squad and would require coordinated member resolution.
 */
export function ChatHeader({ sessionId }: { sessionId: string }) {
  const t = useTranslations("chat.header");
  const tNoAgent = useTranslations("chat");
  const router = useRouter();

  const { data: session } = useSession(sessionId);
  const { data: agents } = useAgents();

  const isSquad = session?.kind === "squad";
  const subjectId = session?.subject_id ?? null;

  const agentById = useMemo(() => {
    const m = new Map<string, NonNullable<typeof agents>[number]>();
    for (const a of agents ?? []) m.set(a.id, a);
    return m;
  }, [agents]);

  const currentAgent =
    !isSquad && subjectId ? agentById.get(subjectId) ?? null : null;

  const handlePick = (agentId: string) => {
    router.push(`/chat/new?agent=${agentId}`);
  };

  return (
    <div className="flex shrink-0 items-center justify-between border-b px-4 py-2">
      {isSquad ? (
        <div className="flex min-w-0 items-center gap-2">
          <div className="flex size-7 items-center justify-center rounded-full bg-black/10 dark:bg-white/10">
            <IconUsersGroup className="size-4" />
          </div>
          <span className="truncate text-sm font-medium">
            {session?.title ?? t("squadBadge")}
          </span>
          <span className="rounded-sm bg-black/10 px-1.5 py-0.5 text-[10px] uppercase tracking-wide sh-muted dark:bg-white/10">
            {t("squadBadge")}
          </span>
        </div>
      ) : (
        <DropdownMenu>
          <DropdownMenuTrigger asChild>
            <button
              type="button"
              aria-label={t("switchAgent")}
              className={cn(
                "group flex min-w-0 items-center gap-2 rounded-md px-2 py-1.5 text-sm",
                "hover:bg-black/5 dark:hover:bg-white/10",
              )}
            >
              <AgentBadge
                avatarUrl={currentAgent?.avatar_url ?? null}
                fallback={currentAgent?.name ?? null}
              />
              <span className="truncate font-medium">
                {currentAgent?.name ?? tNoAgent("noAgent")}
              </span>
              <IconChevronDown className="size-3.5 shrink-0 sh-muted transition-transform group-data-[state=open]:rotate-180" />
            </button>
          </DropdownMenuTrigger>
          <DropdownMenuContent align="start" className="max-h-[60vh] w-72 overflow-y-auto">
            <DropdownMenuLabel>{t("switchAgent")}</DropdownMenuLabel>
            {(agents ?? []).length === 0 ? (
              <div className="px-2 py-3 text-xs sh-muted">{t("noOptions")}</div>
            ) : (
              (agents ?? []).map((a) => (
                <DropdownMenuItem
                  key={a.id}
                  onSelect={() => handlePick(a.id)}
                  className="gap-2"
                >
                  <AgentBadge
                    avatarUrl={a.avatar_url}
                    fallback={a.name}
                    active={a.id === currentAgent?.id}
                  />
                  <div className="flex min-w-0 flex-col">
                    <span className="truncate text-sm">{a.name}</span>
                    {a.description && (
                      <span className="truncate text-[11px] sh-muted">
                        {a.description}
                      </span>
                    )}
                  </div>
                </DropdownMenuItem>
              ))
            )}
            <DropdownMenuSeparator />
            <DropdownMenuItem asChild>
              <Link href="/agents" className="text-xs">
                {t("manageAgents")}
              </Link>
            </DropdownMenuItem>
          </DropdownMenuContent>
        </DropdownMenu>
      )}
    </div>
  );
}

function AgentBadge({
  avatarUrl,
  fallback,
  active = false,
}: {
  avatarUrl: string | null;
  fallback: string | null;
  active?: boolean;
}) {
  const ring = active
    ? "ring-2 ring-[rgb(var(--color-primary))] ring-offset-1"
    : "";
  if (avatarUrl) {
    return (
      // eslint-disable-next-line @next/next/no-img-element
      <img
        src={avatarUrl}
        alt=""
        className={cn("size-6 shrink-0 rounded-full object-cover", ring)}
      />
    );
  }
  const initial = (fallback ?? "?").trim().charAt(0).toUpperCase() || "?";
  return (
    <div
      className={cn(
        "flex size-6 shrink-0 items-center justify-center rounded-full bg-black/10 text-[11px] font-medium dark:bg-white/10",
        ring,
      )}
    >
      {/[A-Za-z0-9]/.test(initial) ? initial : <IconRobot className="size-3.5" />}
    </div>
  );
}
