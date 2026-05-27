"use client";

import { useMemo, useState } from "react";
import { useTranslations } from "next-intl";
import {
  IconChevronDown,
  IconExternalLink,
  IconMessagePlus,
  IconUsersGroup,
} from "@tabler/icons-react";

import { Link, useRouter } from "@/lib/navigation";
import { AgentAvatar } from "@/components/agents/AgentAvatar";
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
 * `AgentSwitcher` — inline agent / squad badge + dropdown picker.
 *
 * Mounted at the top of the left chat sidebar (replaces the plain
 * "Chat" title) so the active agent doubles as the section header.
 * Switching pivots to ``/chat/new?agent=<id>`` because we never
 * mutate ``subject_id`` on an existing session.
 *
 * Two mounting modes mirror the previous `ChatHeader` (now removed):
 *   - `<AgentSwitcher sessionId={...} />` — resolves the agent (or squad)
 *     from the session record (active session in `/chat/[id]`).
 *   - `<AgentSwitcher agentId={...} />` / `<AgentSwitcher squadId={...} />` —
 *     used when a session has not been created yet (draft `/chat/new`).
 */
interface AgentSwitcherProps {
  sessionId?: string | null;
  /** Override agent id when there's no session yet (draft mode). */
  agentId?: string | null;
  /** Override squad id when there's no session yet (draft mode). */
  squadId?: string | null;
  className?: string;
}

export function AgentSwitcher({
  sessionId,
  agentId,
  squadId,
  className,
}: AgentSwitcherProps) {
  const t = useTranslations("chat.header");
  const tNoAgent = useTranslations("chat");
  const router = useRouter();
  const [open, setOpen] = useState(false);

  const { data: session } = useSession(sessionId ?? null);
  const { data: agents } = useAgents();

  const isSquad = sessionId ? session?.kind === "squad" : Boolean(squadId);
  const subjectId = sessionId
    ? session?.subject_id ?? null
    : (agentId ?? null);

  const agentById = useMemo(() => {
    const m = new Map<string, NonNullable<typeof agents>[number]>();
    for (const a of agents ?? []) m.set(a.id, a);
    return m;
  }, [agents]);

  const currentAgent =
    !isSquad && subjectId ? agentById.get(subjectId) ?? null : null;

  const goDetail = (id: string) => {
    setOpen(false);
    router.push(`/agents/${id}`);
  };
  const startNewChat = (id: string) => {
    setOpen(false);
    router.push(`/chat/new?agent=${id}`);
  };

  if (isSquad) {
    return (
      <div className={cn("flex min-w-0 items-center gap-2", className)}>
        <div className="flex size-6 shrink-0 items-center justify-center rounded-full bg-black/10 dark:bg-white/10">
          <IconUsersGroup className="size-3.5" />
        </div>
        <span className="truncate text-sm font-medium">
          {session?.title ?? t("squadBadge")}
        </span>
        <span className="rounded-sm bg-black/10 px-1.5 py-0.5 text-[10px] uppercase tracking-wide sh-muted dark:bg-white/10">
          {t("squadBadge")}
        </span>
      </div>
    );
  }

  return (
    <DropdownMenu open={open} onOpenChange={setOpen}>
      <DropdownMenuTrigger asChild>
        <button
          type="button"
          aria-label={t("switchAgent")}
          data-testid="agent-switcher"
          className={cn(
            "group flex min-w-0 flex-1 items-center gap-2 rounded-md px-1.5 py-1 text-sm",
            "hover:bg-black/5 dark:hover:bg-white/10",
            className,
          )}
        >
          <AgentAvatar
            name={currentAgent?.name}
            avatarUrl={currentAgent?.avatar_url}
            className="size-6"
            fallbackClassName="text-[11px]"
          />
          <span className="min-w-0 flex-1 truncate text-left font-medium">
            {currentAgent?.name ?? tNoAgent("noAgent")}
          </span>
          <IconChevronDown className="size-3.5 shrink-0 sh-muted transition-transform group-data-[state=open]:rotate-180" />
        </button>
      </DropdownMenuTrigger>
      <DropdownMenuContent
        align="start"
        className="max-h-[60vh] w-72 overflow-y-auto"
      >
        <DropdownMenuLabel>{t("switchAgent")}</DropdownMenuLabel>
        {(agents ?? []).length === 0 ? (
          <div className="px-2 py-3 text-xs sh-muted">{t("noOptions")}</div>
        ) : (
          (agents ?? []).map((a) => (
            <div
              key={a.id}
              className="group/row flex items-center gap-2 rounded-sm px-2 py-1.5 text-sm hover:bg-black/5 dark:hover:bg-white/10"
            >
              <button
                type="button"
                onClick={() => startNewChat(a.id)}
                aria-label={t("startNewChat")}
                title={t("startNewChat")}
                className="flex min-w-0 flex-1 items-center gap-2 text-left"
              >
                <AgentAvatar
                  name={a.name}
                  avatarUrl={a.avatar_url}
                  className={cn(
                    "size-6",
                    a.id === currentAgent?.id
                      ? "ring-2 ring-[rgb(var(--color-primary))] ring-offset-1"
                      : "",
                  )}
                  fallbackClassName="text-[11px]"
                />
                <div className="flex min-w-0 flex-col">
                  <span className="truncate text-sm">{a.name}</span>
                  {a.description && (
                    <span className="truncate text-[11px] sh-muted">
                      {a.description}
                    </span>
                  )}
                </div>
              </button>
              <button
                type="button"
                onClick={() => goDetail(a.id)}
                aria-label={t("openDetail")}
                title={t("openDetail")}
                className="flex size-7 shrink-0 items-center justify-center rounded-md sh-muted opacity-0 transition-opacity hover:bg-black/5 focus-visible:opacity-100 group-hover/row:opacity-100 dark:hover:bg-white/10"
              >
                <IconExternalLink className="size-3.5" />
              </button>
            </div>
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
  );
}
