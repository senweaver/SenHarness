"use client";

/**
 * Right-rail "Workspace Panel" container.
 *
 * Layout philosophy:
 *
 *   • Collapsed → renders **nothing**. The chat layout shrinks the
 *     surrounding ``Panel`` to 0 px width, so the message column owns
 *     the entire chat surface. The re-expand affordance lives on the
 *     ChatHeader inside the chat content column (since this pane has
 *     no surface to host a button while it's collapsed).
 *   • Expanded + no session → header bar with just the collapse button
 *     plus a placeholder body. We deliberately don't dangle a share
 *     button because there's nothing to act on yet.
 *   • Expanded + active session → horizontal tab strip on top, with the
 *     **collapse** button anchored to the right edge of that strip, then
 *     the active tab content below. The session title, WS status dot
 *     and share affordance live on the **ChatHeader** (mounted by the
 *     chat layout above the message list).
 *
 * Why the collapse button lives here (rather than in ChatHeader): when
 * the pane is open it's the most visually attached affordance — putting
 * it on the wrong column makes users hunt across the chrome. The expand
 * button has nowhere to go (this aside renders ``null``), so it has to
 * live on the ChatHeader; that's the only asymmetry.
 *
 * All tab content is derived from the live session trace
 * (``useSessionTrace``) — there's only one server round-trip backing the
 * pane. The Trace tab inlines the full chronological replay with role
 * filters, so the chat footer no longer needs to deep-link to ``/traces/<id>``.
 *
 * Auto-open hooks live in the chat page / control hook — they call
 * ``useWorkspacePaneStore.openTab(...)`` when files / approvals / plans
 * arrive, and the panel reacts by flipping ``collapsed`` + ``activeTab``.
 */

import {
  IconActivity,
  IconBrain,
  IconChecklist,
  IconCircleDashed,
  IconClipboardCheck,
  IconExternalLink,
  IconFileText,
  IconLayoutSidebarRightCollapse,
  IconLink,
  IconListCheck,
  IconMessage2,
  IconRobot,
  IconSparkles,
  IconTerminal2,
  IconTool,
  IconUser,
} from "@tabler/icons-react";
import { useTranslations } from "next-intl";
import Link from "next/link";
import { type ReactNode, useMemo, useState } from "react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { SimpleTooltip } from "@/components/ui/tooltip";
import { ApprovalCard } from "@/components/chat/ApprovalCard";
import { TerminalTab } from "@/components/workspace/TerminalTab";
import { cn } from "@/lib/utils";
import { useSessionMessages } from "@/hooks/use-session-messages";
import {
  useSessionTrace,
  type TraceEvent,
  type TraceRole,
} from "@/hooks/use-traces";
import type { MessageRead } from "@/types/api";
import {
  type WorkspaceTab,
  useWorkspacePaneStore,
} from "@/stores/workspace-pane-store";
import {
  useSessionControlStore,
  type ApprovalEntry,
} from "@/stores/session-control-store";

/** Approve/deny callback shape — bound by the session page from
 *  ``useSessionControl().decideApproval`` so this panel never reaches into
 *  ``lib/ws.ts`` directly. Returns ``true`` when the WS frame was sent. */
type DecideApprovalFn = (
  approvalId: string,
  action: "approve" | "deny",
) => boolean;

interface WorkspacePanelProps {
  /** Active session id; ``null`` for the new-chat draft surface. */
  sessionId: string | null;
  /** Approve/deny bridge from ``useSessionControl``. The panel never opens
   *  its own WebSocket — this callback abstracts the control-plane round
   *  trip + local store flip. */
  decideApproval: DecideApprovalFn;
  /** Whether the caller has permission to approve / deny. */
  canDecideApproval: boolean;
  /** Trace deep-link target ID (tool_call_id). */
  traceFocusToolCallId?: string;
  className?: string;
}

const TAB_ORDER: WorkspaceTab[] = [
  "trace",
  "plan",
  "files",
  "sources",
  "memory",
  "approvals",
  "terminal",
];

const TAB_ICONS: Record<WorkspaceTab, typeof IconActivity> = {
  trace: IconActivity,
  plan: IconListCheck,
  files: IconFileText,
  sources: IconLink,
  memory: IconBrain,
  approvals: IconClipboardCheck,
  terminal: IconTerminal2,
};

export function WorkspacePanel({
  sessionId,
  decideApproval,
  canDecideApproval,
  traceFocusToolCallId,
  className,
}: WorkspacePanelProps) {
  const t = useTranslations("chat.workspace");
  const collapsed = useWorkspacePaneStore((s) => s.collapsed);
  const setCollapsed = useWorkspacePaneStore((s) => s.setCollapsed);
  const activeTab = useWorkspacePaneStore((s) => s.activeTab);
  const setActiveTab = useWorkspacePaneStore((s) => s.setActiveTab);
  const collapseLabel = t("collapse");

  // Single source of truth for everything tab-related: the session trace.
  // We only fetch when the pane is open + a session exists, and the existing
  // tab content readers all derive from this one query (no extra requests).
  const traceQuery = useSessionTrace(
    sessionId ?? undefined,
    !collapsed && Boolean(sessionId),
  );
  const messagesQuery = useSessionMessages(
    sessionId ?? undefined,
    !collapsed && Boolean(sessionId),
  );
  const events = useMemo(
    () => traceQuery.data?.events ?? [],
    [traceQuery.data?.events],
  );

  // ─── Collapsed: render NOTHING. ─────────────────────────────────
  // The surrounding ``react-resizable-panels`` Panel shrinks to 0 px
  // when ``collapsed=true`` so there is no rail to host an icon. The
  // re-expand affordance lives in the ChatHeader (mounted by the chat
  // layout above the message list).
  if (collapsed) {
    return null;
  }

  return (
    <aside
      className={cn(
        "@container/workspace flex h-full min-h-0 w-full flex-col border-l bg-[rgb(var(--color-card))]/60",
        className,
      )}
      data-testid="workspace-panel"
      data-collapsed="false"
    >
      {/* Header bar — horizontal tab strip + a trailing collapse button.
          The bar is always rendered (even on the draft surface where
          there are no tabs) so the collapse button has a stable home
          while the pane is open; that's the *only* place the user can
          hide the workspace from once they've opened it. The matching
          expand button lives in ``ChatHeader`` for the collapsed state. */}
      <div className="flex h-12 shrink-0 items-center gap-1 border-b px-1">
        {sessionId ? (
          <div className="sh-scroll-hidden flex min-w-0 flex-1 items-center gap-0.5 overflow-x-auto">
            {TAB_ORDER.map((tab) => {
              const Icon = TAB_ICONS[tab];
              const label = t(`tabs.${tab}`);
              return (
                <SimpleTooltip key={tab} label={label} side="bottom">
                  <Button
                    type="button"
                    size="sm"
                    variant={activeTab === tab ? "subtle" : "ghost"}
                    onClick={() => setActiveTab(tab)}
                    data-testid={`workspace-tab-${tab}`}
                    className="h-7 shrink-0 gap-1 px-1.5 text-[11px] @[360px]/workspace:px-2"
                    aria-pressed={activeTab === tab}
                    aria-label={label}
                  >
                    <Icon className="size-3.5" />
                    <span className="hidden @[360px]/workspace:inline">
                      {label}
                    </span>
                  </Button>
                </SimpleTooltip>
              );
            })}
          </div>
        ) : (
          <span className="flex-1" />
        )}
        <SimpleTooltip label={collapseLabel} side="bottom">
          <Button
            type="button"
            size="icon"
            variant="ghost"
            className="size-7 shrink-0"
            aria-label={collapseLabel}
            onClick={() => setCollapsed(true)}
            data-testid="workspace-pane-collapse"
          >
            <IconLayoutSidebarRightCollapse className="size-3.5" />
          </Button>
        </SimpleTooltip>
      </div>

      {/* Body — empty state when no session, otherwise the active tab. */}
      <div className="min-h-0 flex-1 overflow-y-auto p-3">
        {!sessionId ? (
          <p className="text-xs sh-muted">{t("noSessionHint")}</p>
        ) : (
          <>
            {activeTab === "trace" && (
              <TraceTab
                sessionId={sessionId}
                events={events}
                isLoading={traceQuery.isLoading}
                isError={traceQuery.isError}
                focusToolCallId={traceFocusToolCallId}
              />
            )}
            {activeTab === "plan" && (
              <PlanTab events={events} messages={messagesQuery.data ?? []} />
            )}
            {activeTab === "files" && <FilesTab events={events} />}
            {activeTab === "sources" && <SourcesTab events={events} />}
            {activeTab === "memory" && <MemoryTab events={events} />}
            {activeTab === "approvals" && (
              <ApprovalsTab
                sessionId={sessionId}
                decideApproval={decideApproval}
                canDecideApproval={canDecideApproval}
              />
            )}
            {activeTab === "terminal" && <TerminalTab events={events} />}
          </>
        )}
      </div>
    </aside>
  );
}

// ─── Tabs ──────────────────────────────────────────────────

type TraceFilter = "all" | "messages" | "tools" | "thinking";

function TraceTab({
  sessionId,
  events,
  isLoading,
  isError,
  focusToolCallId,
}: {
  sessionId: string;
  events: TraceEvent[];
  isLoading: boolean;
  isError: boolean;
  focusToolCallId?: string;
}) {
  const t = useTranslations("chat.workspace.trace");
  const [filter, setFilter] = useState<TraceFilter>("all");

  const visible = useMemo(() => {
    if (filter === "all") return events;
    return events.filter((e) => {
      if (filter === "messages")
        return e.role === "user" || e.role === "assistant";
      if (filter === "tools")
        return e.role === "tool_call" || e.role === "tool_result";
      if (filter === "thinking") return e.role === "thinking";
      return true;
    });
  }, [events, filter]);

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center gap-1.5">
        {(["all", "messages", "tools", "thinking"] as const).map((k) => (
          <button
            key={k}
            type="button"
            onClick={() => setFilter(k)}
            className={cn(
              "rounded-full border px-2.5 py-0.5 text-[10px] transition",
              filter === k
                ? "border-[rgb(var(--color-primary))] bg-black/5 text-[rgb(var(--color-primary))] dark:bg-white/5"
                : "sh-muted hover:bg-black/5 dark:hover:bg-white/5",
            )}
          >
            {t(`filter.${k}`)}
          </button>
        ))}
        <Link
          href={`/traces/${sessionId}`}
          target="_blank"
          rel="noreferrer"
          className="ml-auto inline-flex items-center gap-0.5 text-[10px] sh-muted hover:text-[rgb(var(--color-primary))] hover:underline"
        >
          <IconExternalLink className="size-3" />
          {t("openFull")}
        </Link>
      </div>

      {isLoading ? (
        <p className="text-xs sh-muted">{t("loading")}</p>
      ) : isError ? (
        <p className="text-xs text-red-500">{t("loadFailed")}</p>
      ) : visible.length === 0 ? (
        <p className="text-xs sh-muted">{t("empty")}</p>
      ) : (
        <ol className="relative space-y-2 border-l border-black/10 pl-4 dark:border-white/15">
          {visible.map((ev) => (
            <TraceRow
              key={ev.message_id}
              event={ev}
              highlight={
                Boolean(focusToolCallId) &&
                getToolCallId(ev) === focusToolCallId
              }
            />
          ))}
        </ol>
      )}
    </div>
  );
}

const ROLE_META: Record<
  TraceRole,
  {
    tone: "default" | "primary" | "success" | "warning" | "danger" | "outline";
    icon: ReactNode;
  }
> = {
  user: { tone: "primary", icon: <IconUser className="size-3" /> },
  assistant: { tone: "success", icon: <IconRobot className="size-3" /> },
  system: { tone: "outline", icon: <IconCircleDashed className="size-3" /> },
  tool_call: { tone: "default", icon: <IconTool className="size-3" /> },
  tool_result: {
    tone: "default",
    icon: <IconTerminal2 className="size-3" />,
  },
  thinking: { tone: "outline", icon: <IconSparkles className="size-3" /> },
  approval: { tone: "warning", icon: <IconChecklist className="size-3" /> },
  handoff: { tone: "default", icon: <IconMessage2 className="size-3" /> },
};

function TraceRow({
  event,
  highlight,
}: {
  event: TraceEvent;
  highlight?: boolean;
}) {
  const meta = ROLE_META[event.role] ?? ROLE_META.system;
  const text = extractText(event);
  const ts = event.created_at
    ? new Date(event.created_at).toLocaleTimeString()
    : "—";
  const tokenUsage = event.token_usage as {
    tokens?: { input?: number; output?: number };
    latency_ms?: number;
  };
  const verdict = event.metadata?.eval;
  const toolName =
    event.role === "tool_call"
      ? String((event.tool_call as { name?: string } | undefined)?.name ?? "")
      : "";

  return (
    <li className="relative">
      <span className="absolute -left-[10px] top-1.5 flex size-4 items-center justify-center rounded-full border bg-[rgb(var(--color-bg))]">
        {meta.icon}
      </span>
      <div
        className={cn(
          "rounded-md border p-2",
          highlight && "border-[rgb(var(--color-primary))] bg-black/5 dark:bg-white/5",
        )}
      >
        <div className="mb-1 flex flex-wrap items-center gap-1.5 text-[10px]">
          <Badge variant={meta.tone}>{event.role}</Badge>
          {toolName ? (
            <span className="rounded bg-black/5 px-1 font-mono dark:bg-white/10">
              {toolName}
            </span>
          ) : null}
          <span className="sh-muted font-mono">{ts}</span>
          {tokenUsage?.tokens ? (
            <span className="sh-muted font-mono">
              {(tokenUsage.tokens.input ?? 0) + (tokenUsage.tokens.output ?? 0)} tok
            </span>
          ) : null}
          {typeof tokenUsage?.latency_ms === "number" ? (
            <span className="sh-muted font-mono">{tokenUsage.latency_ms}ms</span>
          ) : null}
          {verdict ? (
            <Badge
              variant={
                verdict.verdict === "pass"
                  ? "success"
                  : verdict.verdict === "warn"
                    ? "warning"
                    : "danger"
              }
            >
              {verdict.verdict}
            </Badge>
          ) : null}
        </div>
        {event.role === "tool_call" && event.tool_call ? (
          <pre className="whitespace-pre-wrap break-all rounded bg-black/5 p-1.5 font-mono text-[10px] dark:bg-white/5">
            {safeJson(event.tool_call)}
          </pre>
        ) : null}
        {event.role === "tool_result" && event.tool_result ? (
          <pre className="whitespace-pre-wrap break-all rounded bg-black/5 p-1.5 font-mono text-[10px] dark:bg-white/5">
            {safeJson(event.tool_result)}
          </pre>
        ) : null}
        {event.role === "thinking" && event.thinking ? (
          <p className="whitespace-pre-wrap text-[11px] italic sh-muted">
            {String((event.thinking as Record<string, unknown>).text ?? "")}
          </p>
        ) : null}
        {(event.role === "user" || event.role === "assistant") && text ? (
          <p className="whitespace-pre-wrap text-[12px] leading-relaxed">
            {text}
          </p>
        ) : null}
      </div>
    </li>
  );
}

// ``pydantic-ai-todo`` exposes these five tools. Tool *results* are
// plain strings (no structured state), so we replay the *arguments*
// of each call in chronological order to fold the final list.
const TODO_TOOL_NAMES = new Set([
  "write_todos",
  "read_todos",
  "add_todo",
  "update_todo_status",
  "remove_todo",
]);

type TodoStatus = "pending" | "in_progress" | "completed" | "cancelled";
type TodoItem = { id: string; content: string; status: TodoStatus };

function normalizeTodoStatus(raw: unknown): TodoStatus {
  const s = String(raw ?? "pending");
  if (s === "done" || s === "completed") return "completed";
  if (s === "in_progress") return "in_progress";
  if (s === "cancelled") return "cancelled";
  return "pending";
}

function normalizeTodoBulk(input: unknown): TodoItem[] {
  const list = Array.isArray(input)
    ? input
    : input && typeof input === "object" && Array.isArray((input as { todos?: unknown }).todos)
      ? (input as { todos: unknown[] }).todos
      : [];
  return list
    .map((raw, idx) => {
      const item = raw as {
        id?: string;
        text?: string;
        content?: string;
        title?: string;
        status?: string;
      };
      const content = String(item.content ?? item.text ?? item.title ?? "").trim();
      if (!content) return null;
      return {
        id: item.id ?? `todo-${idx}`,
        content,
        status: normalizeTodoStatus(item.status),
      } as TodoItem;
    })
    .filter((todo): todo is TodoItem => Boolean(todo));
}

function PlanTab({
  events,
  messages,
}: {
  events: TraceEvent[];
  messages: MessageRead[];
}) {
  const t = useTranslations("chat.workspace");

  const todos = useMemo(() => {
    type Invocation = {
      id: string;
      name: string;
      args: Record<string, unknown>;
      result: unknown;
    };
    const invocations: Invocation[] = [];
    const byId = new Map<string, Invocation>();

    const ingest = (payload: unknown) => {
      const evs = Array.isArray(
        (payload as { events?: unknown[] } | null)?.events,
      )
        ? ((payload as { events: unknown[] }).events)
        : [];
      for (const raw of evs) {
        if (!raw || typeof raw !== "object") continue;
        const entry = raw as {
          id?: string;
          name?: string;
          args?: unknown;
          arguments?: unknown;
          result?: unknown;
        };
        const id = typeof entry.id === "string" ? entry.id : "";
        if (!id) continue;
        if (typeof entry.name === "string" && TODO_TOOL_NAMES.has(entry.name)) {
          if (!byId.has(id)) {
            const inv: Invocation = {
              id,
              name: entry.name,
              args: (entry.args ?? entry.arguments ?? {}) as Record<string, unknown>,
              result: undefined,
            };
            invocations.push(inv);
            byId.set(id, inv);
          }
        } else if ("result" in entry) {
          const inv = byId.get(id);
          if (inv) inv.result = entry.result;
        }
      }
    };

    for (const ev of events) {
      if (ev.tool_call) ingest(ev.tool_call);
      if (ev.role === "tool_result" && ev.tool_result) {
        const tr = ev.tool_result as { id?: unknown; result?: unknown };
        if (typeof tr.id === "string") {
          ingest({ events: [tr] });
        }
      }
    }
    if (invocations.length === 0) {
      for (const msg of messages) {
        ingest(msg.tool_call_json);
      }
    }

    const state = new Map<string, TodoItem>();
    const order: string[] = [];
    const upsert = (item: TodoItem) => {
      if (!state.has(item.id)) order.push(item.id);
      state.set(item.id, item);
    };

    for (const inv of invocations) {
      const args = inv.args;
      if (inv.name === "write_todos") {
        state.clear();
        order.length = 0;
        for (const item of normalizeTodoBulk(args)) upsert(item);
        continue;
      }
      if (inv.name === "add_todo") {
        const content = String(args.content ?? "").trim();
        if (!content) continue;
        // The package's add_todo returns "Added todo '...' with ID: <id>"
        // — parse it so subsequent update/remove calls referencing that
        // id can land. Fall back to a synthetic id keyed by call_id.
        const resultText = typeof inv.result === "string" ? inv.result : "";
        const match = /ID:\s*([^\s'")]+)/i.exec(resultText);
        const id = match?.[1] ?? `add-${inv.id}`;
        upsert({ id, content, status: normalizeTodoStatus(args.status) });
        continue;
      }
      if (inv.name === "update_todo_status") {
        const id = String(args.todo_id ?? args.id ?? "");
        if (!id) continue;
        const existing = state.get(id);
        if (!existing) continue;
        upsert({ ...existing, status: normalizeTodoStatus(args.status) });
        continue;
      }
      if (inv.name === "remove_todo") {
        const id = String(args.todo_id ?? args.id ?? "");
        if (!id || !state.has(id)) continue;
        state.delete(id);
        const idx = order.indexOf(id);
        if (idx >= 0) order.splice(idx, 1);
        continue;
      }
      // read_todos: output is a string, no state change to apply.
    }

    return order.map((id) => state.get(id)).filter((t): t is TodoItem => Boolean(t));
  }, [events, messages]);

  if (todos.length === 0) {
    return <p className="text-xs sh-muted">{t("plan.empty")}</p>;
  }
  return (
    <ul className="space-y-1.5 text-xs">
      {todos.map((todo, i) => (
        <li
          key={todo.id}
          className={cn(
            "flex items-start gap-2",
            (todo.status === "completed" || todo.status === "cancelled") &&
              "sh-muted line-through",
          )}
        >
          <span className="mt-0.5 inline-flex size-4 shrink-0 items-center justify-center rounded-full border text-[10px]">
            {todo.status === "completed"
              ? "✓"
              : todo.status === "in_progress"
                ? "·"
                : todo.status === "cancelled"
                  ? "×"
                  : i + 1}
          </span>
          <span>{todo.content}</span>
        </li>
      ))}
    </ul>
  );
}

const FILE_TOOL_NAMES = new Set([
  "write_file",
  "edit_file",
  "delete_file",
  "generate_image",
  "speak",
  "transcribe",
]);

function FilesTab({ events }: { events: TraceEvent[] }) {
  const t = useTranslations("chat.workspace");
  // De-dupe by path/attachment_id so re-edits collapse onto a single row.
  const files = useMemo(() => {
    const out = new Map<
      string,
      { id: string; tool: string; label: string; preview?: unknown; ts: string | null }
    >();
    for (const ev of events) {
      if (ev.role !== "tool_call") continue;
      const call = ev.tool_call as {
        name?: string;
        arguments?: Record<string, unknown>;
      };
      const name = call?.name ?? "";
      if (!FILE_TOOL_NAMES.has(name)) continue;
      const args = call.arguments ?? {};
      const path =
        (args.path as string) ||
        (args.attachment_id as string) ||
        (args.prompt as string) ||
        ev.message_id;
      const key = `${name}:${path}`;
      out.set(key, {
        id: key,
        tool: name,
        label: path,
        preview: args.content ?? args.prompt,
        ts: ev.created_at,
      });
    }
    return Array.from(out.values());
  }, [events]);

  if (files.length === 0) {
    return (
      <div className="space-y-2 text-xs sh-muted">
        <p>{t("files.empty")}</p>
        <p className="text-[10px]">{t("files.scratchHint")}</p>
      </div>
    );
  }
  return (
    <ul className="space-y-2">
      {files.map((f) => (
        <li
          key={f.id}
          className="rounded-lg border bg-[rgb(var(--color-card))] p-2 text-xs"
        >
          <div className="flex items-center gap-1.5">
            <code className="text-[10px] sh-muted">{f.tool}</code>
            <span className="truncate font-medium" title={f.label}>
              {f.label}
            </span>
          </div>
          {f.preview !== undefined ? (
            <pre className="mt-1 max-h-40 overflow-auto rounded bg-black/5 p-1.5 font-mono text-[10px] dark:bg-white/10">
              {typeof f.preview === "string"
                ? f.preview
                : JSON.stringify(f.preview, null, 2)}
            </pre>
          ) : null}
        </li>
      ))}
    </ul>
  );
}

const SOURCE_TOOL_NAMES = new Set([
  "web_search",
  "web_fetch",
  "knowledge_search",
]);

interface SourceItem {
  id: string;
  tool: string;
  url?: string;
  title?: string;
  snippet?: string;
}

function SourcesTab({ events }: { events: TraceEvent[] }) {
  const t = useTranslations("chat.workspace");
  const items = useMemo<SourceItem[]>(() => {
    // We pair tool_call (gives us the query) with the next tool_result for
    // the same id (gives us the actual hits). web_search returns an array of
    // {title, url, snippet}; web_fetch returns a single {url, content};
    // knowledge_search returns {hits: [{doc_title, snippet, score}]}.
    const calls = new Map<string, { name: string; args: Record<string, unknown> }>();
    const out: SourceItem[] = [];

    const pushHits = (
      tool: string,
      callId: string,
      args: Record<string, unknown>,
      result: unknown,
    ) => {
      const r = (result ?? {}) as Record<string, unknown>;
      if (tool === "web_search" && Array.isArray(r.results)) {
        (r.results as Array<Record<string, unknown>>).forEach((hit, i) => {
          out.push({
            id: `${callId}-${i}`,
            tool,
            url: typeof hit.url === "string" ? hit.url : undefined,
            title:
              (typeof hit.title === "string" ? hit.title : undefined) ??
              (typeof hit.url === "string" ? hit.url : undefined),
            snippet:
              typeof hit.snippet === "string" ? hit.snippet : undefined,
          });
        });
        return;
      }
      if (tool === "web_fetch") {
        out.push({
          id: callId,
          tool,
          url: (args.url as string) ?? (typeof r.url === "string" ? r.url : undefined),
          title:
            (typeof r.title === "string" ? r.title : undefined) ??
            (args.url as string),
          snippet:
            typeof r.content === "string"
              ? r.content.slice(0, 240)
              : undefined,
        });
        return;
      }
      if (tool === "knowledge_search" && Array.isArray(r.hits)) {
        (r.hits as Array<Record<string, unknown>>).forEach((hit, i) => {
          out.push({
            id: `${callId}-${i}`,
            tool,
            title:
              (typeof hit.doc_title === "string" ? hit.doc_title : undefined) ??
              (typeof hit.title === "string" ? hit.title : undefined),
            snippet:
              typeof hit.snippet === "string" ? hit.snippet : undefined,
          });
        });
      }
    };

    for (const ev of events) {
      if (ev.role === "tool_call") {
        const call = ev.tool_call as {
          id?: string;
          name?: string;
          arguments?: Record<string, unknown>;
        };
        const id = call?.id ?? ev.message_id;
        const name = call?.name ?? "";
        if (SOURCE_TOOL_NAMES.has(name)) {
          calls.set(id, { name, args: call?.arguments ?? {} });
        }
      } else if (ev.role === "tool_result") {
        const res = ev.tool_result as { id?: string; result?: unknown };
        const id = res?.id ?? ev.message_id;
        const matching = calls.get(id);
        if (matching) {
          pushHits(matching.name, id, matching.args, res?.result);
        }
      }
    }
    return out;
  }, [events]);

  if (items.length === 0) {
    return <p className="text-xs sh-muted">{t("sources.empty")}</p>;
  }
  return (
    <ul className="space-y-2 text-xs">
      {items.map((s) => (
        <li key={s.id} className="rounded-lg border p-2">
          <div className="flex items-center gap-1.5">
            <code className="text-[10px] sh-muted">{s.tool}</code>
            {s.url ? (
              <a
                href={s.url}
                target="_blank"
                rel="noreferrer"
                className="truncate font-medium text-[rgb(var(--color-primary))] hover:underline"
                title={s.url}
              >
                {s.title || s.url}
              </a>
            ) : (
              <span className="truncate font-medium" title={s.title}>
                {s.title || s.id}
              </span>
            )}
          </div>
          {s.snippet ? (
            <p className="mt-1 sh-muted line-clamp-3">{s.snippet}</p>
          ) : null}
        </li>
      ))}
    </ul>
  );
}

const MEMORY_TOOL_NAMES = new Set([
  "memorize",
  "recall",
  "list_memories",
  "session_search",
  "forget",
]);

function MemoryTab({ events }: { events: TraceEvent[] }) {
  const t = useTranslations("chat.workspace");
  const items = useMemo(() => {
    type Item = {
      id: string;
      kind: "memorize" | "recall" | "session_search" | "list_memories" | "forget";
      args: Record<string, unknown>;
      ts: string | null;
      result?: Record<string, unknown> | null;
      status?: string;
      effective?: string;
      headline: string;
    };
    const byCallId = new Map<string, Item>();
    const orderedIds: string[] = [];
    for (const ev of events) {
      if (ev.role === "tool_call") {
        const call = ev.tool_call as {
          id?: string;
          name?: string;
          arguments?: Record<string, unknown>;
        };
        const name = call?.name ?? "";
        if (!MEMORY_TOOL_NAMES.has(name)) continue;
        const callId = String(call?.id ?? ev.message_id);
        const args = call.arguments ?? {};
        const headline =
          (args.text as string) ||
          (args.content as string) ||
          (args.query as string) ||
          (args.key as string) ||
          (args.scope as string) ||
          name;
        if (!byCallId.has(callId)) orderedIds.push(callId);
        byCallId.set(callId, {
          id: callId,
          kind: name as Item["kind"],
          args,
          ts: ev.created_at,
          headline,
        });
      } else if (ev.role === "tool_result") {
        const res = ev.tool_result as {
          id?: string;
          result?: Record<string, unknown> | null;
        };
        const callId = String(res?.id ?? ev.message_id);
        const existing = byCallId.get(callId);
        if (!existing) continue;
        const result = res?.result ?? null;
        existing.result = result;
        if (result && typeof result === "object") {
          const status = (result as { status?: string }).status;
          const effective = (result as { effective?: string }).effective;
          if (typeof status === "string") existing.status = status;
          if (typeof effective === "string") existing.effective = effective;
        }
      }
    }
    const ordered = orderedIds
      .map((id) => byCallId.get(id))
      .filter((it): it is Item => Boolean(it));
    return ordered.reverse(); // newest first
  }, [events]);

  if (items.length === 0) {
    return <p className="text-xs sh-muted">{t("memory.empty")}</p>;
  }
  return (
    <ul className="space-y-2 text-xs">
      {items.map((m) => {
        const labelKey =
          m.kind === "memorize"
            ? "memory.memorize"
            : m.kind === "session_search"
              ? "memory.sessionSearch"
              : "memory.recall";
        const result = m.result;
        const hits =
          result && Array.isArray((result as { hits?: unknown[] }).hits)
            ? ((result as { hits: Record<string, unknown>[] }).hits ?? [])
            : result && Array.isArray((result as { items?: unknown[] }).items)
              ? ((result as { items: Record<string, unknown>[] }).items ?? [])
              : [];
        const tone: "default" | "outline" | "success" | "warning" | "danger" =
          m.status === "applied"
            ? "success"
            : m.status === "deferred"
              ? "outline"
              : m.status === "rejected"
                ? "danger"
                : "outline";
        return (
          <li key={m.id} className="rounded-lg border p-2">
            <div className="flex items-center gap-1.5">
              <Badge variant="outline">{t(labelKey)}</Badge>
              {m.status ? <Badge variant={tone}>{m.status}</Badge> : null}
              {m.effective ? (
                <span className="rounded bg-black/5 px-1 text-[10px] sh-muted dark:bg-white/10">
                  {m.effective}
                </span>
              ) : null}
              <span className="truncate font-medium" title={m.headline}>
                {m.headline}
              </span>
            </div>
            {m.args.scope ? (
              <p className="mt-1 text-[10px] sh-muted">
                scope: {String(m.args.scope)}
              </p>
            ) : null}
            {hits.length > 0 ? (
              <ul className="mt-1 space-y-1">
                {hits.map((hit, i) => (
                  <li
                    key={i}
                    className="rounded bg-black/5 px-1.5 py-1 text-[11px] dark:bg-white/5"
                  >
                    <span className="line-clamp-3">
                      {String(
                        (hit as { content?: string }).content ??
                          (hit as { text?: string }).text ??
                          "",
                      )}
                    </span>
                  </li>
                ))}
              </ul>
            ) : null}
          </li>
        );
      })}
    </ul>
  );
}

// Module-level stable empty array. ``useSyncExternalStore`` requires the
// selector to return the *same* reference when the underlying state is
// unchanged; an inline ``?? []`` literal allocates a fresh array every
// invocation and React 19 throws "getSnapshot should be cached" + an
// infinite render loop. Sharing one frozen array across all callers
// keeps the snapshot stable while ``approvals`` is empty.
const EMPTY_APPROVALS: readonly ApprovalEntry[] = Object.freeze([]);

function ApprovalsTab({
  sessionId,
  decideApproval,
  canDecideApproval,
}: {
  sessionId: string;
  decideApproval: DecideApprovalFn;
  canDecideApproval: boolean;
}) {
  const t = useTranslations("chat.workspace");
  const updateApproval = useSessionControlStore((s) => s.updateApproval);
  const approvals = useSessionControlStore(
    (s) => s.bySession[sessionId]?.approvals ?? EMPTY_APPROVALS,
  );
  if (approvals.length === 0) {
    return <p className="text-xs sh-muted">{t("approvals.empty")}</p>;
  }
  return (
    <div className="space-y-2">
      {approvals.map((a) => (
        <ApprovalCard
          key={a.id}
          approvalId={a.id}
          toolName={a.tool_name}
          toolArgs={a.tool_args}
          summary={a.summary}
          expiresAt={a.expires_at}
          status={a.status}
          canDecide={canDecideApproval}
          onApprove={(id) => {
            // ``decideApproval`` returns false when the socket is gone — we
            // intentionally swallow it here; the ApprovalCard will keep its
            // pending UI and the next ``approval_update`` frame (or the
            // ``onLocalUpdate`` REST deny path) will reconcile.
            decideApproval(id, "approve");
          }}
          onLocalUpdate={(id, action) => {
            updateApproval(sessionId, id, {
              status: action === "approve" ? "approved" : "denied",
            });
          }}
        />
      ))}
    </div>
  );
}

// ─── Helpers ──────────────────────────────────────────────

function extractText(event: TraceEvent): string {
  const c = event.content as Record<string, unknown>;
  if (typeof c.text === "string") return c.text;
  if (Array.isArray(c.parts)) {
    return c.parts
      .map((p) =>
        typeof p === "string"
          ? p
          : (p as Record<string, unknown>).text ?? "",
      )
      .filter(Boolean)
      .join("\n");
  }
  return "";
}

function safeJson(v: unknown): string {
  try {
    const s = JSON.stringify(v, null, 2);
    return s.length > 1200 ? s.slice(0, 1200) + "\n… (truncated)" : s;
  } catch {
    return String(v);
  }
}

function getToolCallId(ev: TraceEvent): string | undefined {
  if (ev.role === "tool_call") {
    return (ev.tool_call as { id?: string } | undefined)?.id;
  }
  if (ev.role === "tool_result") {
    return (ev.tool_result as { id?: string } | undefined)?.id;
  }
  return undefined;
}
