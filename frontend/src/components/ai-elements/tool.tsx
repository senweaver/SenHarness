"use client";

/**
 * Renders one tool-call lifecycle (input → executing → output / error) as a
 * collapsed status row that expands to JSON detail. Keeps a colour-coded
 * status pill so users can scan a long transcript without expanding each row.
 *
 * Drives directly off the chat transport's tool-part shape.
 */

import {
  IconChevronDown,
  IconCircleCheck,
  IconCircleX,
  IconClock,
  IconLoader2,
  IconShieldX,
  IconTool,
} from "@tabler/icons-react";
import { useState, type ReactNode } from "react";
import { useTranslations } from "next-intl";

import { cn } from "@/lib/utils";

type ToolState =
  | "input-streaming"
  | "input-available"
  | "output-available"
  | "output-error";

interface ToolProps {
  /** Stable id used for keying + accessibility. */
  toolCallId: string;
  /** Name of the tool — e.g. ``read_file``. */
  toolName: string;
  /** Lifecycle stage; drives the status pill + spinner. */
  state: ToolState;
  /** Tool input arguments (rendered as JSON inside the expandable detail). */
  input?: unknown;
  /** Tool output (or error message when state === ``output-error``). */
  output?: unknown;
  /** Friendly error message — overrides default i18n when provided. */
  errorText?: string | null;
  /** Optional truncation hint set by the backend overflow guard. */
  truncated?: boolean;
  className?: string;
  /** Default-collapsed; pass ``true`` to start open (e.g. on user click). */
  defaultOpen?: boolean;
  /** Optional secondary actions (rendered in the header right slot). */
  actions?: ReactNode;
}

export function Tool({
  toolCallId,
  toolName,
  state,
  input,
  output,
  errorText,
  truncated,
  className,
  defaultOpen = false,
  actions,
}: ToolProps) {
  const [open, setOpen] = useState(defaultOpen);
  const memoryStatus = readMemoryStatus(toolName, output);

  return (
    <div
      className={cn(
        "rounded-lg border bg-[rgb(var(--color-card))]/40 text-xs",
        className,
      )}
      data-testid="tool-card"
      data-tool-call-id={toolCallId}
      data-tool-state={state}
    >
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="flex w-full items-center gap-2 px-3 py-1.5"
        aria-expanded={open}
      >
        <ToolStatusBadge state={state} />
        <code className="font-mono text-[11px] text-[rgb(var(--color-primary))]">
          {toolName}
        </code>
        {truncated ? (
          <span className="ml-1 rounded bg-amber-500/10 px-1 py-px text-[10px] text-amber-600">
            truncated
          </span>
        ) : null}
        {memoryStatus ? <MemoryStatusBadge status={memoryStatus} /> : null}
        <span className="ml-auto flex items-center gap-1.5">
          {actions}
          <IconChevronDown
            className={cn(
              "size-3 transition-transform",
              open && "rotate-180",
            )}
          />
        </span>
      </button>
      {open ? (
        <div className="border-t px-3 py-2">
          {state === "output-error" && errorText ? (
            <div className="mb-2 rounded bg-red-500/10 px-2 py-1 text-[11px] text-red-600">
              {errorText}
            </div>
          ) : null}
          {input !== undefined ? (
            <DetailBlock label="input" value={input} />
          ) : null}
          {output !== undefined ? (
            <DetailBlock label="output" value={output} />
          ) : null}
        </div>
      ) : null}
    </div>
  );
}

function ToolStatusBadge({ state }: { state: ToolState }) {
  if (state === "input-streaming" || state === "input-available") {
    return (
      <span className="flex items-center gap-1 sh-muted">
        <IconLoader2 className="size-3 animate-spin" />
      </span>
    );
  }
  if (state === "output-error") {
    return (
      <span className="flex items-center gap-1 text-red-500">
        <IconCircleX className="size-3" />
      </span>
    );
  }
  return (
    <span className="flex items-center gap-1 text-emerald-600">
      <IconCircleCheck className="size-3" />
    </span>
  );
}

type MemoryStatus = "deferred" | "rejected";

function readMemoryStatus(toolName: string, output: unknown): MemoryStatus | null {
  if (toolName !== "memorize") return null;
  if (!output || typeof output !== "object") return null;
  const status = (output as { status?: unknown }).status;
  if (status === "deferred") return "deferred";
  if (status === "rejected") return "rejected";
  return null;
}

function MemoryStatusBadge({ status }: { status: MemoryStatus }) {
  const t = useTranslations("memory");
  if (status === "deferred") {
    return (
      <span
        className="ml-1 inline-flex items-center gap-1 rounded bg-blue-500/10 px-1.5 py-px text-[10px] text-blue-600"
        title={t("toolDeferredBadgeTooltip")}
      >
        <IconClock className="size-3" />
        {t("toolDeferredBadge")}
      </span>
    );
  }
  return (
    <span
      className="ml-1 inline-flex items-center gap-1 rounded bg-amber-500/10 px-1.5 py-px text-[10px] text-amber-700"
      title={t("toolRejectedBadgeTooltip")}
    >
      <IconShieldX className="size-3" />
      {t("toolRejectedBadge")}
    </span>
  );
}

function DetailBlock({ label, value }: { label: string; value: unknown }) {
  let pretty: string;
  try {
    pretty =
      typeof value === "string"
        ? value
        : JSON.stringify(value, null, 2);
  } catch {
    pretty = String(value);
  }
  // Cap the visible body to keep big payloads from wrecking the page; the
  // user can still scroll inside the pre. Backend already truncates at the
  // boundary set by ``tool_output_max_chars``.
  return (
    <div className="mb-1 last:mb-0">
      <div className="mb-0.5 flex items-center gap-1.5 text-[10px] uppercase tracking-wider sh-muted">
        <IconTool className="size-3" />
        <span>{label}</span>
      </div>
      <pre className="max-h-72 overflow-auto whitespace-pre-wrap rounded bg-black/5 p-2 font-mono text-[11px] dark:bg-white/5">
        {pretty}
      </pre>
    </div>
  );
}
