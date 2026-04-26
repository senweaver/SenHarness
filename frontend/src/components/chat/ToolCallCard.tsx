"use client";

import { useState } from "react";
import {
  IconAlertCircle,
  IconCalculator,
  IconCheck,
  IconChevronDown,
  IconChevronUp,
  IconClock,
  IconExternalLink,
  IconFileText,
  IconLoader2,
  IconSearch,
  IconTool,
  IconWorld,
} from "@tabler/icons-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import { CopyButton } from "./CopyButton";
import { MarkdownContent } from "./MarkdownContent";

export type ToolStatus = "pending" | "running" | "completed" | "error";

export interface ToolCall {
  id: string;
  name: string;
  args: Record<string, unknown>;
  result?: unknown;
  status: ToolStatus;
}

interface ToolCallCardProps {
  toolCall: ToolCall;
  className?: string;
}

// ────────────────────────────────────────────────────────────
// Status pill helpers
// ────────────────────────────────────────────────────────────

function statusIcon(status: ToolStatus) {
  if (status === "completed") {
    return <IconCheck className="size-3.5 text-green-600 dark:text-green-400" />;
  }
  if (status === "error") {
    return <IconAlertCircle className="size-3.5 text-red-600 dark:text-red-400" />;
  }
  return <IconLoader2 className="size-3.5 animate-spin sh-muted" />;
}

// ────────────────────────────────────────────────────────────
// Specialised renderers (per tool name)
// ────────────────────────────────────────────────────────────

/** `current_time` → `{ iso, timezone, unix, weekday }`. */
function CurrentTimeRenderer({ result }: { result: unknown }) {
  if (!result || typeof result !== "object") return null;
  const r = result as Record<string, unknown>;
  const iso = typeof r.iso === "string" ? r.iso : null;
  const tz = typeof r.timezone === "string" ? r.timezone : null;
  const weekday = typeof r.weekday === "string" ? r.weekday : null;
  if (!iso) return null;
  const date = new Date(iso);
  const dateStr = isNaN(date.getTime())
    ? iso.slice(0, 10)
    : date.toLocaleDateString(undefined, {
        year: "numeric",
        month: "short",
        day: "numeric",
      });
  const timeStr = isNaN(date.getTime())
    ? iso.slice(11, 19)
    : date.toLocaleTimeString(undefined, {
        hour: "2-digit",
        minute: "2-digit",
        second: "2-digit",
      });
  return (
    <div className="flex flex-wrap items-center gap-3 py-1">
      <div className="flex items-center gap-1.5">
        <IconClock className="size-4 text-[rgb(var(--color-primary))]" />
        <span className="text-sm font-semibold tabular-nums">{timeStr}</span>
      </div>
      <span className="sh-muted text-xs">{dateStr}</span>
      {weekday && <Badge variant="outline">{weekday}</Badge>}
      {tz && (
        <span className="text-[10px] sh-muted font-mono">{tz}</span>
      )}
    </div>
  );
}

/** `web_search` → `{ query, provider, results: [...] }`. */
function WebSearchRenderer({
  result,
  query,
}: {
  result: unknown;
  query?: string;
}) {
  if (!result || typeof result !== "object") return null;
  const r = result as Record<string, unknown>;
  const items = Array.isArray(r.results)
    ? (r.results as Array<Record<string, unknown>>)
    : [];
  const provider = typeof r.provider === "string" ? r.provider : null;
  const note = typeof r.note === "string" ? r.note : null;
  if (items.length === 0) {
    return (
      <div className="flex flex-col gap-1 py-2 text-xs sh-muted">
        <div className="flex items-center gap-1.5">
          <IconWorld className="size-3.5" />
          <span>No results</span>
          {provider && (
            <Badge variant="outline" className="ml-1">
              {provider}
            </Badge>
          )}
        </div>
        {note && <p className="text-[11px] italic">{note}</p>}
      </div>
    );
  }
  return (
    <div className="space-y-2 py-1">
      <div className="flex items-center gap-1.5 text-[11px] sh-muted">
        <IconWorld className="size-3.5" />
        <span>
          {items.length} result{items.length === 1 ? "" : "s"}
          {query ? <span className="ml-1 italic">for &ldquo;{query}&rdquo;</span> : null}
        </span>
        {provider && (
          <Badge variant="outline" className="ml-auto">
            {provider}
          </Badge>
        )}
      </div>
      <ul className="space-y-1.5">
        {items.map((it, idx) => {
          const title = typeof it.title === "string" ? it.title : "(no title)";
          const url = typeof it.url === "string" ? it.url : "";
          const snippet = typeof it.snippet === "string" ? it.snippet : "";
          const source = typeof it.source === "string" ? it.source : "";
          return (
            <li
              key={`${url}-${idx}`}
              className="rounded-md border bg-black/[0.02] dark:bg-white/[0.02] p-2"
            >
              <div className="flex items-start gap-1.5">
                <Badge variant="outline" className="mt-0.5 shrink-0">
                  {idx + 1}
                </Badge>
                <div className="min-w-0 flex-1">
                  <div className="text-xs font-medium truncate">{title}</div>
                  {url && (
                    <a
                      href={url}
                      target="_blank"
                      rel="noopener noreferrer"
                      className="mt-0.5 inline-flex items-center gap-1 text-[10px] text-[rgb(var(--color-primary))] hover:underline truncate max-w-full"
                    >
                      <IconExternalLink className="size-2.5 shrink-0" />
                      <span className="truncate">{source || url}</span>
                    </a>
                  )}
                  {snippet && (
                    <p className="mt-1 text-[11px] sh-muted line-clamp-2">
                      {snippet}
                    </p>
                  )}
                </div>
              </div>
            </li>
          );
        })}
      </ul>
    </div>
  );
}

/** `web_fetch` → `{ url, ok, title, body, truncated_body }` or error. */
function WebFetchRenderer({ result }: { result: unknown }) {
  const [expanded, setExpanded] = useState(false);
  if (!result || typeof result !== "object") return null;
  const r = result as Record<string, unknown>;
  const ok = r.ok === true;
  const url = typeof r.url === "string" ? r.url : "";
  const title = typeof r.title === "string" ? r.title : "";
  const body = typeof r.body === "string" ? r.body : "";
  const truncated = r.truncated_body === true;

  if (!ok) {
    const errMsg =
      typeof r.message === "string"
        ? r.message
        : typeof r.error === "string"
          ? r.error
          : "Fetch failed";
    return (
      <div className="flex flex-col gap-1 py-1 text-xs">
        <div className="flex items-center gap-1.5 text-red-600 dark:text-red-400">
          <IconAlertCircle className="size-3.5" />
          <span>Fetch failed</span>
        </div>
        {url && (
          <a
            href={url}
            target="_blank"
            rel="noopener noreferrer"
            className="text-[10px] text-[rgb(var(--color-primary))] hover:underline truncate"
          >
            {url}
          </a>
        )}
        <p className="text-[11px] sh-muted">{errMsg}</p>
      </div>
    );
  }

  return (
    <div className="space-y-1.5 py-1">
      <div className="flex items-start gap-1.5">
        <IconWorld className="size-3.5 shrink-0 mt-0.5 sh-muted" />
        <div className="min-w-0 flex-1">
          {title && (
            <div className="text-xs font-medium truncate">{title}</div>
          )}
          {url && (
            <a
              href={url}
              target="_blank"
              rel="noopener noreferrer"
              className="inline-flex items-center gap-1 text-[10px] text-[rgb(var(--color-primary))] hover:underline truncate max-w-full"
            >
              <IconExternalLink className="size-2.5 shrink-0" />
              <span className="truncate">{url}</span>
            </a>
          )}
        </div>
        {truncated && (
          <Badge variant="warning" className="shrink-0">
            truncated
          </Badge>
        )}
      </div>
      {body && (
        <>
          <div
            className={cn(
              "rounded-md border bg-black/[0.02] dark:bg-white/[0.02] px-2.5 py-2 overflow-hidden",
              expanded ? "max-h-[600px] overflow-y-auto" : "max-h-32",
            )}
          >
            <MarkdownContent content={body} className="text-[12px]" />
          </div>
          <button
            type="button"
            onClick={() => setExpanded((v) => !v)}
            className="text-[10px] sh-muted hover:text-[rgb(var(--color-fg))] inline-flex items-center gap-0.5"
          >
            {expanded ? (
              <>
                <IconChevronUp className="size-3" /> Collapse
              </>
            ) : (
              <>
                <IconChevronDown className="size-3" /> Expand
              </>
            )}
          </button>
        </>
      )}
    </div>
  );
}

/** `knowledge_search` → `{ ok, collection_name, hits: [...] }`. */
function KnowledgeSearchRenderer({
  result,
  query,
}: {
  result: unknown;
  query?: string;
}) {
  const [expandedIdx, setExpandedIdx] = useState<number | null>(null);
  if (!result || typeof result !== "object") return null;
  const r = result as Record<string, unknown>;
  if (r.ok === false) {
    const err = typeof r.error === "string" ? r.error : "Search failed";
    return (
      <div className="flex items-center gap-1.5 py-2 text-xs sh-muted">
        <IconAlertCircle className="size-3.5" />
        <span>{err}</span>
      </div>
    );
  }
  const hits = Array.isArray(r.hits)
    ? (r.hits as Array<Record<string, unknown>>)
    : [];
  const colName =
    typeof r.collection_name === "string" ? r.collection_name : null;
  if (hits.length === 0) {
    return (
      <div className="flex items-center gap-1.5 py-2 text-xs sh-muted">
        <IconSearch className="size-3.5" />
        <span>No relevant documents</span>
      </div>
    );
  }

  return (
    <div className="space-y-2 py-1">
      <div className="flex flex-wrap items-center gap-1.5 text-[11px] sh-muted">
        <IconSearch className="size-3.5" />
        <span>
          {hits.length} hit{hits.length === 1 ? "" : "s"}
          {query ? <span className="ml-1 italic">for &ldquo;{query}&rdquo;</span> : null}
        </span>
        {colName && (
          <Badge variant="outline" className="ml-1">
            {colName}
          </Badge>
        )}
      </div>
      <div className="flex gap-2 overflow-x-auto pb-1">
        {hits.map((hit, idx) => {
          const score = typeof hit.score === "number" ? hit.score : 0;
          const docTitle =
            typeof hit.doc_title === "string" ? hit.doc_title : "(untitled)";
          const text = typeof hit.text === "string" ? hit.text : "";
          const ord = typeof hit.ord === "number" ? hit.ord : null;
          const scoreColor =
            score >= 0.7
              ? "text-green-600 dark:text-green-400"
              : score >= 0.4
                ? "text-amber-600 dark:text-amber-400"
                : "text-red-600 dark:text-red-400";
          return (
            <button
              type="button"
              key={`${docTitle}-${idx}`}
              onClick={() =>
                setExpandedIdx(expandedIdx === idx ? null : idx)
              }
              className={cn(
                "min-w-[220px] max-w-[280px] shrink-0 rounded-md border bg-black/[0.02] dark:bg-white/[0.02] p-2 text-left transition-colors hover:bg-black/[0.05] dark:hover:bg-white/[0.05]",
                expandedIdx === idx &&
                  "ring-2 ring-[rgb(var(--color-primary))]",
              )}
            >
              <div className="mb-1 flex items-center gap-1">
                <IconFileText className="size-3 shrink-0 sh-muted" />
                <span className="text-[11px] font-medium truncate flex-1">
                  {docTitle}
                </span>
                <span
                  className={cn(
                    "text-[10px] font-mono tabular-nums",
                    scoreColor,
                  )}
                >
                  {score.toFixed(2)}
                </span>
              </div>
              <div className="flex items-center gap-1 mb-1">
                <Badge variant="outline">[{idx + 1}]</Badge>
                {ord != null && (
                  <Badge variant="outline">chunk {ord}</Badge>
                )}
              </div>
              <p className="text-[11px] sh-muted line-clamp-2">{text}</p>
            </button>
          );
        })}
      </div>
      {expandedIdx !== null &&
        hits[expandedIdx] &&
        (() => {
          const hit = hits[expandedIdx];
          const text = typeof hit.text === "string" ? hit.text : "";
          const docTitle =
            typeof hit.doc_title === "string"
              ? hit.doc_title
              : "(untitled)";
          return (
            <div className="rounded-md border-2 border-[rgb(var(--color-primary))]/30 bg-[rgb(var(--color-primary))]/5 p-2.5">
              <div className="mb-1.5 flex items-center justify-between">
                <div className="flex items-center gap-1.5">
                  <Badge variant="primary">[{expandedIdx + 1}]</Badge>
                  <span className="text-xs font-medium">{docTitle}</span>
                </div>
                <Button
                  variant="ghost"
                  size="icon"
                  className="size-6"
                  onClick={() => setExpandedIdx(null)}
                  aria-label="collapse"
                >
                  <IconChevronUp className="size-3.5" />
                </Button>
              </div>
              <p className="text-xs leading-relaxed whitespace-pre-wrap">
                {text}
              </p>
            </div>
          );
        })()}
    </div>
  );
}

/** `calculator` → `{ expression, value }` or `{ expression, error }`. */
function CalculatorRenderer({ result }: { result: unknown }) {
  if (!result || typeof result !== "object") return null;
  const r = result as Record<string, unknown>;
  const expression = typeof r.expression === "string" ? r.expression : "";
  if (typeof r.error === "string") {
    return (
      <div className="py-1 text-xs">
        <code className="font-mono text-[11px]">{expression}</code>
        <div className="mt-1 flex items-center gap-1 text-red-600 dark:text-red-400">
          <IconAlertCircle className="size-3.5" />
          <span>{r.error}</span>
        </div>
      </div>
    );
  }
  const value = r.value;
  return (
    <div className="flex items-center gap-2 py-1 text-sm">
      <IconCalculator className="size-4 text-[rgb(var(--color-primary))]" />
      <code className="font-mono text-[12px] sh-muted">{expression}</code>
      <span className="font-mono">=</span>
      <span className="font-mono font-semibold tabular-nums">
        {typeof value === "number" || typeof value === "string"
          ? String(value)
          : JSON.stringify(value)}
      </span>
    </div>
  );
}

/** Generic raw fallback — args + result as pretty JSON. */
function RawRenderer({
  args,
  result,
}: {
  args: Record<string, unknown>;
  result?: unknown;
}) {
  const argText = JSON.stringify(args, null, 2);
  const resultText =
    result === undefined
      ? ""
      : typeof result === "string"
        ? result
        : JSON.stringify(result, null, 2);
  return (
    <div className="space-y-1.5">
      <div className="group/raw relative">
        <div className="mb-1 flex items-center justify-between">
          <span className="text-[10px] uppercase tracking-wide sh-muted">
            args
          </span>
          <CopyButton
            text={argText}
            className="opacity-0 group-hover/raw:opacity-100"
          />
        </div>
        <pre className="overflow-x-auto rounded bg-black/5 dark:bg-white/5 p-2 text-[11px] font-mono">
          {argText || "{}"}
        </pre>
      </div>
      {resultText !== "" && (
        <div className="group/raw relative">
          <div className="mb-1 flex items-center justify-between">
            <span className="text-[10px] uppercase tracking-wide sh-muted">
              result
            </span>
            <CopyButton
              text={resultText}
              className="opacity-0 group-hover/raw:opacity-100"
            />
          </div>
          <pre className="max-h-48 overflow-x-auto overflow-y-auto rounded bg-black/5 dark:bg-white/5 p-2 text-[11px] font-mono">
            {resultText}
          </pre>
        </div>
      )}
    </div>
  );
}

// ────────────────────────────────────────────────────────────
// Tool name → icon + display title
// ────────────────────────────────────────────────────────────

function toolMeta(name: string): {
  icon: React.ReactNode;
  title: string;
} {
  switch (name) {
    case "current_time":
      return {
        icon: <IconClock className="size-3.5 text-[rgb(var(--color-primary))]" />,
        title: "Current time",
      };
    case "calculator":
      return {
        icon: (
          <IconCalculator className="size-3.5 text-[rgb(var(--color-primary))]" />
        ),
        title: "Calculator",
      };
    case "web_search":
      return {
        icon: <IconWorld className="size-3.5 text-[rgb(var(--color-primary))]" />,
        title: "Web search",
      };
    case "web_fetch":
      return {
        icon: <IconWorld className="size-3.5 text-[rgb(var(--color-primary))]" />,
        title: "Fetch URL",
      };
    case "knowledge_search":
      return {
        icon: <IconSearch className="size-3.5 text-[rgb(var(--color-primary))]" />,
        title: "Knowledge search",
      };
    case "session_search":
      return {
        icon: <IconSearch className="size-3.5 text-[rgb(var(--color-primary))]" />,
        title: "Session search",
      };
    default:
      return { icon: <IconTool className="size-3.5 sh-muted" />, title: name };
  }
}

// ────────────────────────────────────────────────────────────
// Main card
// ────────────────────────────────────────────────────────────

export function ToolCallCard({ toolCall, className }: ToolCallCardProps) {
  const [showRaw, setShowRaw] = useState(false);
  const meta = toolMeta(toolCall.name);
  const completed = toolCall.status === "completed";
  const errored = toolCall.status === "error";
  const queryArg =
    typeof toolCall.args.query === "string" ? toolCall.args.query : undefined;

  const hasSpecial = (() => {
    if (!completed) return false;
    return [
      "current_time",
      "calculator",
      "web_search",
      "web_fetch",
      "knowledge_search",
    ].includes(toolCall.name);
  })();

  const renderSpecial = () => {
    switch (toolCall.name) {
      case "current_time":
        return <CurrentTimeRenderer result={toolCall.result} />;
      case "calculator":
        return <CalculatorRenderer result={toolCall.result} />;
      case "web_search":
        return (
          <WebSearchRenderer result={toolCall.result} query={queryArg} />
        );
      case "web_fetch":
        return <WebFetchRenderer result={toolCall.result} />;
      case "knowledge_search":
        return (
          <KnowledgeSearchRenderer result={toolCall.result} query={queryArg} />
        );
      default:
        return null;
    }
  };

  return (
    <div
      className={cn(
        "rounded-lg border sh-card overflow-hidden",
        errored && "border-red-300 dark:border-red-900",
        className,
      )}
      data-testid="tool-call-card"
      data-tool-name={toolCall.name}
      data-tool-status={toolCall.status}
    >
      <div className="flex items-center gap-1.5 border-b px-2.5 py-1.5 bg-black/[0.02] dark:bg-white/[0.02]">
        {meta.icon}
        <span className="text-xs font-medium">{meta.title}</span>
        {(toolCall.name === "web_search" ||
          toolCall.name === "knowledge_search" ||
          toolCall.name === "session_search") &&
          queryArg && (
            <span className="text-[11px] sh-muted italic truncate max-w-[200px]">
              &ldquo;{queryArg}&rdquo;
            </span>
          )}
        <div className="ml-auto flex items-center gap-1">
          {hasSpecial && completed && (
            <Button
              variant="ghost"
              size="icon"
              className="size-5"
              onClick={() => setShowRaw((v) => !v)}
              title={showRaw ? "Show formatted" : "Show raw"}
              aria-label="Toggle raw view"
            >
              {showRaw ? (
                <IconChevronUp className="size-3" />
              ) : (
                <IconChevronDown className="size-3" />
              )}
            </Button>
          )}
          {statusIcon(toolCall.status)}
        </div>
      </div>
      <div className="px-2.5 py-2">
        {hasSpecial && !showRaw ? (
          renderSpecial() ?? <RawRenderer args={toolCall.args} result={toolCall.result} />
        ) : (
          <RawRenderer args={toolCall.args} result={toolCall.result} />
        )}
      </div>
    </div>
  );
}
