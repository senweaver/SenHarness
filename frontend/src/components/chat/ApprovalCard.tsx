"use client";

import { useState } from "react";
import {
  IconAlertTriangle,
  IconCheck,
  IconLock,
  IconShieldCheck,
  IconX,
} from "@tabler/icons-react";
import { useTranslations } from "next-intl";
import { Button } from "@/components/ui/button";
import { useCountdown } from "@/hooks/use-countdown";
import { cn } from "@/lib/utils";
import { DecisionDialog, type DecisionAction } from "@/components/approvals/DecisionDialog";
import { CopyButton } from "./CopyButton";

export type ApprovalStatus =
  | "pending"
  | "approved"
  | "denied"
  | "expired"
  | "cancelled";

interface ApprovalCardProps {
  approvalId: string;
  toolName: string;
  toolArgs: Record<string, unknown>;
  summary?: string | null;
  expiresAt?: string;
  status: ApprovalStatus;
  canDecide: boolean;
  /** Quick approve via WS (no reason needed). */
  onApprove: (approvalId: string) => void;
  /** Mark optimistically after REST deny succeeds. */
  onLocalUpdate: (approvalId: string, action: "approve" | "deny") => void;
}

/**
 * `ApprovalCard` — inline HITL gate rendered in the chat transcript.
 *
 * Approve goes through WebSocket (cheap, no reason). Deny requires a reason
 * and uses the shared `DecisionDialog` which posts via REST + signals the
 * approval manager so the runner resumes immediately.
 */
export function ApprovalCard({
  approvalId,
  toolName,
  toolArgs,
  summary,
  expiresAt,
  status,
  canDecide,
  onApprove,
  onLocalUpdate,
}: ApprovalCardProps) {
  const t = useTranslations("chat.approval");
  const [dialog, setDialog] = useState<{
    open: boolean;
    action: DecisionAction;
  }>({ open: false, action: "deny" });
  const { label: countdownLabel, totalMs, expired } = useCountdown(
    expiresAt ?? null,
  );

  const pending = status === "pending";
  const approved = status === "approved";
  const denied = status === "denied";
  const cancelled = status === "cancelled";
  const isExpired = status === "expired" || (pending && expired);

  const urgency: "red" | "amber" | "neutral" = isExpired
    ? "red"
    : totalMs <= 60_000
      ? "red"
      : totalMs <= 120_000
        ? "amber"
        : "neutral";

  const argsJson = JSON.stringify(toolArgs, null, 2);

  return (
    <div
      className="rounded-lg border-2 border-amber-400 bg-amber-50/60 dark:bg-amber-950/30 dark:border-amber-700 p-3"
      data-testid="approval-card"
      data-tool-name={toolName}
      data-approval-status={status}
    >
      <div className="flex items-center gap-2">
        <IconShieldCheck className="size-4 text-amber-700 dark:text-amber-300 shrink-0" />
        <span className="text-xs font-medium text-amber-900 dark:text-amber-200">
          {t("needConfirm")}
        </span>
        <code className="text-xs font-mono font-semibold text-amber-900 dark:text-amber-200">
          {toolName}
        </code>
        {approved && (
          <span className="ml-auto inline-flex items-center gap-1 text-xs text-green-700 dark:text-green-400">
            <IconCheck className="size-3.5" /> {t("approved")}
          </span>
        )}
        {denied && (
          <span className="ml-auto inline-flex items-center gap-1 text-xs text-red-700 dark:text-red-400">
            <IconX className="size-3.5" /> {t("denied")}
          </span>
        )}
        {cancelled && (
          <span className="ml-auto text-xs sh-muted">cancelled</span>
        )}
        {isExpired && !approved && !denied && !cancelled && (
          <span className="ml-auto inline-flex items-center gap-1 text-xs sh-muted">
            <IconAlertTriangle className="size-3.5" /> {t("expired")}
          </span>
        )}
      </div>

      {summary && (
        <p className="mt-1.5 text-[11px] sh-muted font-mono break-all">
          {summary}
        </p>
      )}

      <details className="mt-2 group/args">
        <summary className="cursor-pointer text-[11px] sh-muted hover:text-[rgb(var(--color-fg))] inline-flex items-center gap-1">
          {t("argsLabel")}
        </summary>
        <div className="mt-1 group/raw relative">
          <div className="absolute right-1 top-1 opacity-0 group-hover/raw:opacity-100">
            <CopyButton text={argsJson} />
          </div>
          <pre className="overflow-x-auto rounded bg-black/5 dark:bg-white/10 p-2 text-[11px] font-mono">
            {argsJson || "{}"}
          </pre>
        </div>
      </details>

      {pending && (
        <div className="mt-2 flex flex-wrap items-center gap-2">
          {canDecide ? (
            <>
              <Button
                size="sm"
                className="h-7 bg-green-600 hover:bg-green-700 text-white"
                onClick={() => onApprove(approvalId)}
                data-testid="approval-approve"
              >
                <IconCheck className="size-3" /> {t("approve")}
              </Button>
              <Button
                size="sm"
                variant="destructive"
                className="h-7"
                onClick={() => setDialog({ open: true, action: "deny" })}
                data-testid="approval-deny"
              >
                <IconX className="size-3" /> {t("deny")}
              </Button>
            </>
          ) : (
            <span className="inline-flex items-center gap-1 rounded-full bg-black/5 dark:bg-white/10 px-2 py-1 text-[10px] sh-muted">
              <IconLock className="size-3" />
              {t("noPermission")}
            </span>
          )}
          {expiresAt && (
            <span
              className={cn(
                "ml-auto inline-flex items-center gap-1 font-mono tabular-nums text-[11px]",
                urgency === "red" && "text-rose-600 dark:text-rose-400",
                urgency === "amber" && "text-amber-600 dark:text-amber-400",
                urgency === "neutral" && "sh-muted",
              )}
              data-testid="approval-countdown"
            >
              <IconAlertTriangle className="size-3" />
              {isExpired ? t("expired") : countdownLabel}
            </span>
          )}
        </div>
      )}

      <DecisionDialog
        approvalId={approvalId}
        action={dialog.action}
        summary={summary}
        open={dialog.open}
        onOpenChange={(o) => setDialog((d) => ({ ...d, open: o }))}
        onDecided={() => onLocalUpdate(approvalId, dialog.action)}
      />
    </div>
  );
}
