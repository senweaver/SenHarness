"use client";

/**
 * "Terminal" tab — IDE-style transcript of every ``shell`` tool call this
 * session has performed.
 *
 * We do **not** open a separate WebSocket or PTY. The runtime ``shell``
 * tool ([backend/app/agents/tools/shell.py](backend/app/agents/tools/shell.py))
 * emits a normal ``tool_call`` + ``tool_result`` pair that already flows
 * through the session trace. The Terminal tab is purely a rendering
 * concern: pull those pairs out of the trace and present them in a
 * monospace `<pre>` block keyed off the call id.
 *
 * Empty state nudges the user to enable the optional shell tool and
 * pick the Docker sandbox — those are the two preconditions for the
 * tool to actually run (see ``run_shell`` in the backend).
 */

import { useMemo } from "react";
import { IconTerminal2 } from "@tabler/icons-react";
import { useTranslations } from "next-intl";

import { cn } from "@/lib/utils";
import type { TraceEvent } from "@/hooks/use-traces";

interface TerminalEntry {
  id: string;
  command: string;
  cwd: string | null;
  ok: boolean | null;
  exitCode: number | null;
  stdout: string;
  stderr: string;
  reason: string | null;
  ts: string | null;
}

export function TerminalTab({ events }: { events: TraceEvent[] }) {
  const t = useTranslations("chat.workspace.terminal");

  const entries = useMemo<TerminalEntry[]>(() => {
    // Pair tool_call (gives us the command + cwd) with the matching
    // tool_result (gives us stdout / stderr / exit code). Order is
    // chronological; we render newest at the bottom so the latest
    // command is always in view (the panel auto-scrolls via
    // overflow-y-auto in the parent body).
    const calls = new Map<
      string,
      { command: string; cwd: string | null; ts: string | null }
    >();
    const out: TerminalEntry[] = [];
    for (const ev of events) {
      if (ev.role === "tool_call") {
        const call = ev.tool_call as {
          id?: string;
          name?: string;
          arguments?: Record<string, unknown>;
        };
        if (call?.name !== "shell") continue;
        const id = call.id ?? ev.message_id;
        const args = call.arguments ?? {};
        calls.set(id, {
          command: typeof args.command === "string" ? args.command : "",
          cwd: typeof args.cwd === "string" ? args.cwd : null,
          ts: ev.created_at,
        });
        out.push({
          id,
          command: typeof args.command === "string" ? args.command : "",
          cwd: typeof args.cwd === "string" ? args.cwd : null,
          ok: null,
          exitCode: null,
          stdout: "",
          stderr: "",
          reason: null,
          ts: ev.created_at,
        });
      } else if (ev.role === "tool_result") {
        const res = ev.tool_result as { id?: string; result?: unknown };
        const id = res?.id ?? ev.message_id;
        const matching = calls.get(id);
        if (!matching) continue;
        const result = (res?.result ?? {}) as Record<string, unknown>;
        // Find the entry we just inserted (by id) and merge the result in.
        // We don't expect duplicate ids per run, so a linear scan from the
        // end is fine and keeps the chronological order intact.
        for (let i = out.length - 1; i >= 0; i--) {
          if (out[i]!.id === id) {
            out[i] = {
              ...out[i]!,
              ok:
                typeof result.ok === "boolean"
                  ? result.ok
                  : null,
              exitCode:
                typeof result.exit_code === "number"
                  ? result.exit_code
                  : null,
              stdout:
                typeof result.stdout === "string" ? result.stdout : "",
              stderr:
                typeof result.stderr === "string" ? result.stderr : "",
              reason:
                typeof result.reason === "string" ? result.reason : null,
            };
            break;
          }
        }
      }
    }
    return out;
  }, [events]);

  if (entries.length === 0) {
    return (
      <div className="flex flex-col items-center gap-2 px-2 py-6 text-center text-xs sh-muted">
        <IconTerminal2 className="size-5" />
        <p>{t("empty")}</p>
        <p className="text-[10px]">{t("enableHint")}</p>
      </div>
    );
  }

  return (
    <div className="space-y-2">
      {entries.map((e) => (
        <TerminalEntryCard key={e.id} entry={e} />
      ))}
    </div>
  );
}

function TerminalEntryCard({ entry }: { entry: TerminalEntry }) {
  const t = useTranslations("chat.workspace.terminal");
  const ts = entry.ts ? new Date(entry.ts).toLocaleTimeString() : "—";
  const status =
    entry.ok === null
      ? t("statusRunning")
      : entry.ok
        ? t("statusOk", { code: entry.exitCode ?? 0 })
        : entry.reason
          ? t("statusFailedReason", { reason: entry.reason })
          : t("statusFailed", { code: entry.exitCode ?? -1 });
  const statusClass =
    entry.ok === null
      ? "text-amber-600 dark:text-amber-400"
      : entry.ok
        ? "text-emerald-600 dark:text-emerald-400"
        : "text-rose-600 dark:text-rose-400";
  return (
    <div className="rounded-md border bg-[rgb(var(--color-card))] p-2">
      <div className="mb-1 flex flex-wrap items-center gap-1.5 text-[10px]">
        <span className="font-mono sh-muted">{ts}</span>
        {entry.cwd ? (
          <span className="rounded bg-black/5 px-1 font-mono dark:bg-white/10">
            {entry.cwd}
          </span>
        ) : null}
        <span className={cn("font-mono", statusClass)}>{status}</span>
      </div>
      <pre className="whitespace-pre-wrap break-all rounded bg-black/90 p-2 font-mono text-[11px] leading-snug text-emerald-300 dark:bg-black/60">
        <span className="text-emerald-500">$ </span>
        {entry.command || ""}
      </pre>
      {entry.stdout ? (
        <pre className="mt-1 max-h-64 overflow-auto whitespace-pre-wrap break-all rounded bg-black/5 p-1.5 font-mono text-[10px] dark:bg-white/5">
          {entry.stdout}
        </pre>
      ) : null}
      {entry.stderr ? (
        <pre className="mt-1 max-h-32 overflow-auto whitespace-pre-wrap break-all rounded bg-rose-500/10 p-1.5 font-mono text-[10px] text-rose-700 dark:text-rose-300">
          {entry.stderr}
        </pre>
      ) : null}
    </div>
  );
}
