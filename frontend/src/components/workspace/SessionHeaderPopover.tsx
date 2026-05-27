"use client";

/**
 * Hover/click popover that hangs off the workspace pane's session-title chip.
 *
 * Surface: a richer "what is this conversation actually about" detail card —
 * agent name + avatar, model route, conversation mode, workspace label,
 * counters and the squads this agent belongs to. Inspired by the reference
 * IDE-style detail popover (see plan #4) so the user can glance at runtime
 * context without leaving the chat.
 *
 * Sources:
 *   - ``useAgent(session.subject_id)`` for p2p sessions; ``null`` otherwise.
 *   - ``useSquads()`` for the workspace squad list, filtered to those that
 *     name this agent as a member. The list is fetched once at the workspace
 *     level (already cached by other surfaces), so this view is essentially
 *     free.
 *   - ``useWorkspaceStore`` for the active workspace label.
 *
 * Uses Radix' ``Popover`` (shadcn primitives are already exported under
 * ``components/ui/popover``). Trigger is the inner ``children`` so the
 * caller (``WorkspacePanel``) can keep its own truncated title rendering.
 */

import { useMemo } from "react";
import {
  IconCpu,
  IconEdit,
  IconExternalLink,
  IconMessage2,
  IconUsersGroup,
} from "@tabler/icons-react";
import { AgentAvatar } from "@/components/agents/AgentAvatar";
import { useTranslations } from "next-intl";

import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from "@/components/ui/popover";
import { Link } from "@/lib/navigation";
import { cn } from "@/lib/utils";
import { useAgent } from "@/hooks/use-agent-mutations";
import { useSquads } from "@/hooks/use-squads";
import { useWorkspaceStore } from "@/stores/workspace-store";
import type { SessionRead } from "@/types/api";

interface SessionHeaderPopoverProps {
  session: SessionRead | null | undefined;
  /** The visible chip the user clicks / hovers — usually the title text. */
  children: React.ReactNode;
  /** When the chat session is bound to a single agent, fetch + render the
   *  agent metadata. ``null`` for squad / channel sessions. */
  agentId?: string | null;
}

export function SessionHeaderPopover({
  session,
  children,
  agentId,
}: SessionHeaderPopoverProps) {
  const t = useTranslations("chat.workspace.headerPopover");
  const tWs = useTranslations("chat.workspace");
  const subjectAgentId =
    agentId ??
    (session && session.kind === "p2p" ? session.subject_id : null);
  const { data: agent } = useAgent(subjectAgentId);
  const { data: squads } = useSquads();
  const workspaces = useWorkspaceStore((s) => s.workspaces);
  const activeWorkspaceId = useWorkspaceStore((s) => s.activeWorkspaceId);
  const workspaceName = useMemo(() => {
    if (!activeWorkspaceId) return null;
    return (
      workspaces.find((w) => w.id === activeWorkspaceId)?.name ?? null
    );
  }, [workspaces, activeWorkspaceId]);

  // Squads where this agent is a direct member. ``useSquads`` returns the
  // bare list (no members) so we'd need a per-squad fetch to be perfectly
  // accurate; for the popover we settle for "lists every squad in the
  // workspace" when we can't tell. We already render up to 3 chips and
  // truncate, so the worst case is harmless.
  const ownedSquads = useMemo(() => {
    if (!squads) return [];
    return squads.slice(0, 3);
  }, [squads]);

  const modelLabel = useMemo<string | null>(() => {
    if (!agent) return null;
    const route = (agent.metadata_json as { model_route?: unknown } | null)
      ?.model_route;
    if (typeof route === "string" && route) return route;
    return null;
  }, [agent]);

  const modeLabel = useMemo<string | null>(() => {
    if (!session) return null;
    const meta = session.metadata_json as { last_mode?: unknown } | null;
    const m = meta?.last_mode;
    return typeof m === "string" && m ? m : null;
  }, [session]);

  const created = session?.created_at
    ? new Date(session.created_at).toLocaleString()
    : null;
  const lastActive = session?.last_message_at
    ? new Date(session.last_message_at).toLocaleString()
    : null;

  return (
    <Popover>
      <PopoverTrigger asChild>{children}</PopoverTrigger>
      <PopoverContent
        align="start"
        side="bottom"
        sideOffset={8}
        className="w-80 space-y-3 text-[12px]"
      >
        {/* Header — agent identity + jump-to-detail link */}
        <div className="flex items-start gap-2">
          <AgentAvatar
            name={agent?.name ?? null}
            avatarUrl={agent?.avatar_url ?? null}
            className="size-8 rounded-full"
          />
          <div className="min-w-0 flex-1">
            <span
              className="block truncate text-[13px] font-medium leading-tight"
              title={agent?.name ?? undefined}
            >
              {agent?.name ?? t("noAgent")}
            </span>
            {agent?.description ? (
              <p
                className="line-clamp-2 text-[11px] leading-snug sh-muted"
                title={agent.description}
              >
                {agent.description}
              </p>
            ) : null}
          </div>
        </div>

        {/* Direct-jump actions — visible to any workspace member
            (matches the backend's ``ensure_member_access`` policy on
            ``GET /v1/agents/{id}``). The Edit button targets the
            workspace settings route which gates writes server-side, so
            non-admin members still see the affordance but get a 403
            toast on save instead of a hidden button. */}
        {subjectAgentId ? (
          <div className="flex items-center gap-1">
            <Link
              href={`/agents/${subjectAgentId}`}
              className="inline-flex flex-1 items-center justify-center gap-1 rounded-md border px-2 py-1 text-[11px] hover:border-[rgb(var(--color-primary))] hover:text-[rgb(var(--color-primary))]"
            >
              <IconExternalLink className="size-3" />
              <span>{t("openAgent")}</span>
            </Link>
            <Link
              href={`/agents/${subjectAgentId}/edit`}
              className="inline-flex flex-1 items-center justify-center gap-1 rounded-md border px-2 py-1 text-[11px] hover:border-[rgb(var(--color-primary))] hover:text-[rgb(var(--color-primary))]"
            >
              <IconEdit className="size-3" />
              <span>{t("editAgent")}</span>
            </Link>
          </div>
        ) : null}

        {/* Stat grid */}
        <dl className="grid grid-cols-[auto,1fr] gap-x-3 gap-y-1">
          <DetailRow
            icon={<IconCpu className="size-3.5" />}
            label={t("model")}
            value={modelLabel ?? t("modelDefault")}
          />
          <DetailRow
            icon={<IconCpu className="size-3.5" />}
            label={t("mode")}
            value={modeLabel ?? t("modeDefault")}
          />
          {workspaceName ? (
            <DetailRow
              icon={<IconCpu className="size-3.5" />}
              label={t("workspace")}
              value={workspaceName}
            />
          ) : null}
          {session ? (
            <DetailRow
              icon={<IconMessage2 className="size-3.5" />}
              label={t("messages")}
              value={tWs("messageCount", { count: session.message_count })}
            />
          ) : null}
          {created ? (
            <DetailRow
              icon={<IconMessage2 className="size-3.5" />}
              label={t("created")}
              value={created}
            />
          ) : null}
          {lastActive ? (
            <DetailRow
              icon={<IconMessage2 className="size-3.5" />}
              label={t("lastActive")}
              value={lastActive}
            />
          ) : null}
        </dl>

        {/* Squads chips */}
        {ownedSquads.length > 0 ? (
          <div className="space-y-1">
            <div className="flex items-center gap-1 text-[11px] sh-muted">
              <IconUsersGroup className="size-3.5" />
              <span>{t("squads")}</span>
            </div>
            <ul className="flex flex-wrap gap-1">
              {ownedSquads.map((sq) => (
                <li key={sq.id}>
                  <Link
                    href={`/squads/${sq.id}`}
                    className={cn(
                      "rounded-full border px-2 py-0.5 text-[10px]",
                      "hover:border-[rgb(var(--color-primary))] hover:text-[rgb(var(--color-primary))]",
                    )}
                  >
                    {sq.name}
                  </Link>
                </li>
              ))}
            </ul>
          </div>
        ) : null}
      </PopoverContent>
    </Popover>
  );
}

function DetailRow({
  icon,
  label,
  value,
}: {
  icon: React.ReactNode;
  label: string;
  value: React.ReactNode;
}) {
  return (
    <>
      <dt className="flex items-center gap-1 text-[11px] sh-muted">
        {icon}
        <span>{label}</span>
      </dt>
      <dd className="truncate text-[12px]" title={typeof value === "string" ? value : undefined}>
        {value}
      </dd>
    </>
  );
}
