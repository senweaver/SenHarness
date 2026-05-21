"use client";

import { useTranslations } from "next-intl";

import type { WorkspaceRuntimeSummary } from "@/types/api";

export interface WorkspaceStatusRowProps {
  workspaceName: string;
  summary: WorkspaceRuntimeSummary | null;
}

/** Two-line workspace switcher status row (variant D2).
 *
 *  Renders one colored dot + number per non-zero counter:
 *
 *  - green dot + ``running`` count (only when > 0)
 *  - amber dot + ``stuck`` count (only when > 0)
 *  - rose  dot + ``orphan`` count (only when > 0)
 *
 *  Falls back to the ``idle`` label when every counter is zero so the
 *  row keeps a stable two-line height. ``queued`` is treated as live
 *  activity for the idle check but is intentionally not surfaced as a
 *  separate chip (the design only carves out the three colors above).
 */
export function WorkspaceStatusRow({
  workspaceName,
  summary,
}: WorkspaceStatusRowProps) {
  const t = useTranslations("workspaceSwitcher");
  const running = summary?.running ?? 0;
  const stuck = summary?.stuck ?? 0;
  const orphan = summary?.orphan ?? 0;
  const queued = summary?.queued ?? 0;
  const isIdle = running === 0 && stuck === 0 && orphan === 0 && queued === 0;

  const ariaSummaryParts: string[] = [];
  if (running > 0)
    ariaSummaryParts.push(t("status.running", { count: running }));
  if (stuck > 0) ariaSummaryParts.push(t("status.stuck", { count: stuck }));
  if (orphan > 0) ariaSummaryParts.push(t("status.orphan", { count: orphan }));
  if (isIdle) ariaSummaryParts.push(t("status.idle"));
  const aria = t("status.statusForWorkspace", {
    name: workspaceName,
    summary: ariaSummaryParts.join(", "),
  });

  return (
    <span
      role="status"
      aria-label={aria}
      data-testid="workspace-status-row"
      className="flex h-[14px] items-center gap-2 text-[10px] sh-muted"
    >
      {isIdle ? (
        <span>{t("status.idle")}</span>
      ) : (
        <>
          {running > 0 && (
            <span
              className="flex items-center gap-1 tabular-nums"
              data-testid="workspace-status-running"
            >
              <span
                aria-hidden
                className="size-1.5 rounded-full bg-emerald-500"
              />
              <span>{running}</span>
            </span>
          )}
          {stuck > 0 && (
            <span
              className="flex items-center gap-1 tabular-nums"
              data-testid="workspace-status-stuck"
            >
              <span
                aria-hidden
                className="size-1.5 rounded-full bg-amber-500"
              />
              <span>{stuck}</span>
            </span>
          )}
          {orphan > 0 && (
            <span
              className="flex items-center gap-1 tabular-nums"
              data-testid="workspace-status-orphan"
            >
              <span
                aria-hidden
                className="size-1.5 rounded-full bg-rose-500"
              />
              <span>{orphan}</span>
            </span>
          )}
        </>
      )}
    </span>
  );
}
