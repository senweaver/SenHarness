"use client";

/**
 * Chat-content header.
 *
 * Lives at the top of the **chat content column** (NOT inside the right-rail
 * workspace panel). It hosts the session title popover, the WS health pip
 * and the share affordance.
 *
 * Layout:
 *
 *   ┌─────────────────────────────────────────────────────────────┐
 *   │ ●  Session title (popover trigger)   [Share]  [Expand?]     │
 *   └─────────────────────────────────────────────────────────────┘
 *
 * Visually transparent: no border, no backdrop. The header sits above the
 * scroll area (it does not overlap with it), so we don't need a background
 * to keep titles readable while scrolling. The result blends naturally
 * into the surrounding chat surface.
 *
 * Title sizing: the popover trigger hugs its content (no ``flex-1``) so a
 * short session title doesn't stretch a button across the full chat
 * column. Trailing space stays empty / clickable through to nothing.
 *
 * Toggle button: only rendered when the workspace pane is **collapsed**
 * (so users always have a way to re-expand it). When the pane is open
 * the collapse affordance lives at the right edge of the workspace tab
 * strip (see ``WorkspacePanel``) — that keeps the chrome attached to the
 * thing it's controlling instead of dangling on the wrong column.
 */

import { useMemo, useState } from "react";
import {
  IconLayoutSidebarLeftExpand,
  IconLayoutSidebarRightExpand,
  IconSettings,
  IconTarget,
} from "@tabler/icons-react";
import { useTranslations } from "next-intl";

import { Button } from "@/components/ui/button";
import { SimpleTooltip } from "@/components/ui/tooltip";
import { AgentAvatar } from "@/components/agents/AgentAvatar";
import { ArtifactDebugPanel } from "@/components/chat/ArtifactDebugPanel";
import { SessionGoalDialog } from "@/components/chat/SessionGoalDialog";
import { ShareDialog } from "@/components/chat/ShareDialog";
import { PendingMemoriesDrawer } from "@/components/sessions/PendingMemoriesDrawer";
import { SessionHeaderPopover } from "@/components/workspace/SessionHeaderPopover";
import { cn } from "@/lib/utils";
import { useAgent } from "@/hooks/use-agent-mutations";
import { useActiveSessionGoal } from "@/hooks/use-session-goals";
import { useSession } from "@/hooks/use-sessions";
import { Link } from "@/lib/navigation";
import {
  useSessionControlStore,
  type WsPhase,
} from "@/stores/session-control-store";
import { useSidebarStore } from "@/stores/sidebar-store";
import { useWorkspacePaneStore } from "@/stores/workspace-pane-store";

interface ChatHeaderProps {
  /** Active session id; ``null`` for the new-chat draft surface. */
  sessionId: string | null;
  /** Optional agent context (only used on the draft surface, where we
   *  don't yet have a backing session). The popover falls back to this
   *  to render the agent identity for a brand-new conversation. */
  agentId?: string | null;
}

export function ChatHeader({ sessionId, agentId }: ChatHeaderProps) {
  const t = useTranslations("chat.workspace");
  const tSessionList = useTranslations("chat.sessionList");
  const tGoal = useTranslations("sessionGoal");
  const collapsed = useWorkspacePaneStore((s) => s.collapsed);
  const setCollapsed = useWorkspacePaneStore((s) => s.setCollapsed);
  const sessionListCollapsed = useSidebarStore(
    (s) => s.chatSessionListCollapsed,
  );
  const setSessionListCollapsed = useSidebarStore(
    (s) => s.setChatSessionListCollapsed,
  );

  // ─── Goal lock entry point. When no goal is locked the
  // ``SessionGoalBanner`` collapses to nothing, so the only way to
  // discover the lock dialog is this header button. ``useActiveSessionGoal``
  // is already cached by the banner (same query key), so this hook does
  // not create an extra round-trip.
  const goalQ = useActiveSessionGoal(sessionId);
  const [goalDialogOpen, setGoalDialogOpen] = useState(false);
  const hasLockedGoal = Boolean(goalQ.data);

  // Only fetch session metadata when we have an id; the new-chat surface
  // and the bare /chat index pass null and we just render the placeholder.
  const { data: session } = useSession(sessionId);

  const titleText = useMemo(() => {
    if (!sessionId) return t("title");
    return session?.title?.trim() || t("untitledSession");
  }, [sessionId, session?.title, t]);
  const subtitleText = useMemo(() => {
    if (!session) return null;
    const count = session.message_count ?? 0;
    return count > 0 ? t("messageCount", { count }) : t("emptySession");
  }, [session, t]);

  // ─── WS phase → status-dot styling. Four states:
  //    • ``connecting`` → amber pulse while the handshake is in flight
  //    • ``open``       → steady emerald during a live stream
  //    • ``closed``     → muted grey *idle* (this WS is request-response
  //                        — it's closed between turns by design)
  //    • ``error``      → rose pulse when preflight / mid-stream socket
  //                        actually fails; clickable to retry
  //    The chat session page mirrors the live socket lifecycle into the
  //    store via the transport's onSocket* hooks. ───
  const wsPhase = useSessionControlStore((s): WsPhase | null =>
    sessionId ? s.bySession[sessionId]?.wsPhase ?? "closed" : null,
  );
  const setWsPhase = useSessionControlStore((s) => s.setWsPhase);

  const expandLabel = t("expand");

  // Agent-avatar shortcut. Resolves the same way the popover does
  // (explicit ``agentId`` on the draft surface; ``session.subject_id``
  // for live p2p sessions). On click → direct jump to the agent detail
  // page, skipping the popover. This is the primary "I want to look at
  // / edit this agent" affordance now that the title chip itself has
  // grown a richer dropdown.
  const subjectAgentId =
    agentId ??
    (session && session.kind === "p2p" ? session.subject_id : null);
  const { data: agentMeta } = useAgent(subjectAgentId);

  return (
    <div
      className={cn(
        "flex h-12 shrink-0 items-center gap-1 px-2",
      )}
      data-testid="chat-header"
    >
      {sessionListCollapsed ? (
        <SimpleTooltip label={tSessionList("expand")} side="bottom">
          <Button
            type="button"
            size="icon"
            variant="ghost"
            className="size-7"
            aria-label={tSessionList("expand")}
            onClick={() => setSessionListCollapsed(false)}
            data-testid="chat-header-sessionlist-toggle"
          >
            <IconLayoutSidebarLeftExpand className="size-3.5" />
          </Button>
        </SimpleTooltip>
      ) : null}
      {subjectAgentId ? (
        <SimpleTooltip
          label={agentMeta?.name ?? t("openAgent")}
          side="bottom"
        >
          <Link
            href={`/agents/${subjectAgentId}`}
            className="inline-flex shrink-0 items-center justify-center rounded-full transition hover:opacity-90"
            aria-label={t("openAgent")}
            data-testid="chat-header-agent-avatar"
          >
            <AgentAvatar
              name={agentMeta?.name}
              avatarUrl={agentMeta?.avatar_url}
              className="size-7"
              fallbackClassName="text-[12px]"
            />
          </Link>
        </SimpleTooltip>
      ) : null}
      {sessionId ? (
        <SessionHeaderPopover session={session ?? null} agentId={agentId}>
          <span
            role="button"
            tabIndex={0}
            className={cn(
              // Width hugs content so a short title doesn't stretch a
              // button bar across the whole chat column. ``max-w-full``
              // keeps long titles from punching through into the
              // trailing actions (truncation kicks in instead).
              "inline-flex max-w-full min-w-0 cursor-pointer flex-col justify-center rounded-md px-1.5 py-1 text-left transition",
              "hover:bg-black/5 dark:hover:bg-white/10 focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-[rgb(var(--color-primary))]",
            )}
            data-testid="chat-header-title-trigger"
          >
            <span className="flex items-center gap-1.5">
              {wsPhase ? (
                <WsStatusDot
                  phase={wsPhase}
                  onRetry={
                    sessionId && wsPhase === "error"
                      ? () => setWsPhase(sessionId, "closed")
                      : undefined
                  }
                />
              ) : null}
              <span
                className="truncate text-[12px] font-medium leading-tight"
                title={titleText ?? undefined}
                data-testid="chat-header-title"
              >
                {titleText}
              </span>
            </span>
            {subtitleText ? (
              <span className="block truncate text-[10px] sh-muted leading-tight">
                {subtitleText}
              </span>
            ) : null}
          </span>
        </SessionHeaderPopover>
      ) : (
        <span className="truncate px-1.5 text-[12px] font-medium sh-muted">
          {titleText}
        </span>
      )}

      {/* Trailing actions live on the right; the spacer eats remaining
          width so they hug the edge regardless of how short the title is. */}
      <div className="ml-auto flex items-center gap-1">
        {sessionId ? <ShareDialog sessionId={sessionId} /> : null}

        {sessionId && !hasLockedGoal ? (
          <SimpleTooltip label={tGoal("openLockDialog")} side="bottom">
            <Button
              type="button"
              size="icon"
              variant="ghost"
              className="size-7"
              aria-label={tGoal("openLockDialog")}
              onClick={() => setGoalDialogOpen(true)}
              data-testid="chat-header-lock-goal"
            >
              <IconTarget className="size-3.5" />
            </Button>
          </SimpleTooltip>
        ) : null}

        {sessionId ? <ArtifactDebugPanel sessionId={sessionId} /> : null}
        {sessionId ? <PendingMemoriesDrawer sessionId={sessionId} /> : null}

        {subjectAgentId ? (
          <SimpleTooltip label={t("agentSettings")} side="bottom">
            <Button
              asChild
              type="button"
              size="icon"
              variant="ghost"
              className="size-7"
              aria-label={t("agentSettings")}
              data-testid="chat-header-agent-settings"
            >
              <Link href={`/agents/${subjectAgentId}`}>
                <IconSettings className="size-3.5" />
              </Link>
            </Button>
          </SimpleTooltip>
        ) : null}

        {/* Only the *expand* affordance lives here. The collapse button
            now belongs to the workspace pane itself (anchored to its
            tab strip), so we don't double up while the pane is open. */}
        {collapsed ? (
          <SimpleTooltip label={expandLabel} side="bottom">
            <Button
              type="button"
              size="icon"
              variant="ghost"
              className="size-7"
              aria-label={expandLabel}
              onClick={() => setCollapsed(false)}
              data-testid="chat-header-workspace-toggle"
            >
              <IconLayoutSidebarRightExpand className="size-3.5" />
            </Button>
          </SimpleTooltip>
        ) : null}
      </div>

      {sessionId ? (
        <SessionGoalDialog
          sessionId={sessionId}
          open={goalDialogOpen}
          onOpenChange={setGoalDialogOpen}
        />
      ) : null}
    </div>
  );
}

/**
 * Four-state WS lifecycle pip:
 *
 *   • ``connecting`` → amber pulse while the handshake is in flight
 *   • ``open``       → solid emerald during a live stream
 *   • ``closed``     → muted neutral pip (idle / ready)
 *   • ``error``      → rose pulse when the socket actually fails;
 *                       clickable to clear the error so the next turn
 *                       can attempt a fresh connect
 *
 * Why ``closed`` is **not** rose: the chat WebSocket is short-lived by
 * design — it opens for a single turn and closes once the model finishes.
 * The vast majority of a session's wall-clock time is spent with the
 * socket "closed" but perfectly healthy. A red error pip would be on
 * permanently and train users to ignore it. A muted neutral pip keeps
 * the lifecycle visible without screaming "broken"; rose is reserved for
 * the genuine ``error`` state that warrants user action.
 */
function WsStatusDot({
  phase,
  onRetry,
}: {
  phase: WsPhase;
  /** Bound only when ``phase === "error"`` — clears the error so the
   *  next user send re-attempts a fresh connect. */
  onRetry?: () => void;
}) {
  const t = useTranslations("chat.workspace.wsStatus");
  const dotClass =
    phase === "open"
      ? "bg-emerald-500"
      : phase === "connecting"
        ? "bg-amber-500 animate-pulse"
        : phase === "error"
          ? "bg-rose-500 animate-pulse"
          : "bg-zinc-400 dark:bg-zinc-500";
  const label =
    phase === "open"
      ? t("open")
      : phase === "connecting"
        ? t("connecting")
        : phase === "error"
          ? t("error")
          : t("closed");
  // ``ring-current/20`` lifts the dot out of low-contrast surfaces
  // (subtle hover backgrounds, busy avatars) without leaning on a
  // hard-coded ring colour — it inherits whatever foreground class
  // wraps the trigger.
  const dot = (
    <span
      className={cn(
        "size-2 shrink-0 rounded-full ring-1 ring-current/20",
        dotClass,
      )}
      role="status"
      aria-label={label}
    />
  );
  if (onRetry) {
    // Stop propagation so the parent (session title popover trigger)
    // doesn't open when the user is clicking the pip to retry.
    return (
      <SimpleTooltip label={label} side="bottom">
        <button
          type="button"
          onClick={(e) => {
            e.stopPropagation();
            e.preventDefault();
            onRetry();
          }}
          aria-label={label}
          className="inline-flex items-center justify-center rounded-full focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-rose-500/40"
          data-testid="ws-status-retry"
        >
          {dot}
        </button>
      </SimpleTooltip>
    );
  }
  return (
    <SimpleTooltip label={label} side="bottom">
      {dot}
    </SimpleTooltip>
  );
}
