"use client";

import { useEffect, useState } from "react";
import { IconLoader2, IconShieldCheck, IconShieldX } from "@tabler/icons-react";
import { useTranslations } from "next-intl";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { useDecideApproval } from "@/hooks/use-approvals";

export type DecisionAction = "approve" | "deny";

interface Props {
  approvalId: string;
  action: DecisionAction;
  /** Optional, shown as description under the title for context. */
  summary?: string | null;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** Fires after the server confirms the decision. */
  onDecided?: () => void;
}

const MIN_DENY_REASON = 3;

/**
 * `DecisionDialog` — shared confirmation dialog for approve/deny.
 *
 * * **Approve** flow: opens, posts immediately on confirm (no reason required).
 * * **Deny** flow: requires a non-empty reason (≥3 chars) before the button
 *   enables, because audit logs should capture *why* a human blocked a tool
 *   call. The backend already accepts a free-form string; this is a UX gate.
 *
 * Used by the chat inline card, the `/approvals` queue, and the settings
 * history page — any place that needs a uniform HITL decision UX.
 */
export function DecisionDialog({
  approvalId,
  action,
  summary,
  open,
  onOpenChange,
  onDecided,
}: Props) {
  const t = useTranslations("approvals.denyDialog");
  const tCommon = useTranslations("approvals");
  const decide = useDecideApproval();
  const [reason, setReason] = useState("");

  useEffect(() => {
    if (!open) setReason("");
  }, [open]);

  const isDeny = action === "deny";
  const canSubmit =
    !decide.isPending &&
    (!isDeny || reason.trim().length >= MIN_DENY_REASON);

  const submit = async () => {
    try {
      await decide.mutateAsync({
        approvalId,
        action,
        reason: isDeny ? reason.trim() : "",
      });
      toast.success(
        isDeny ? tCommon("toastDenied") : tCommon("toastApproved"),
      );
      onOpenChange(false);
      onDecided?.();
    } catch {
      toast.error(tCommon("toastFailed"));
    }
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            {isDeny ? (
              <IconShieldX className="size-4 text-rose-500" />
            ) : (
              <IconShieldCheck className="size-4 text-emerald-500" />
            )}
            {isDeny ? t("title") : tCommon("confirmApproveTitle")}
          </DialogTitle>
          {summary ? (
            <DialogDescription className="line-clamp-2 font-mono">
              {summary}
            </DialogDescription>
          ) : (
            <DialogDescription>
              {isDeny
                ? t("description")
                : tCommon("confirmApproveDescription")}
            </DialogDescription>
          )}
        </DialogHeader>

        {isDeny ? (
          <div className="grid gap-1.5">
            <Label htmlFor="deny-reason">{t("reasonLabel")}</Label>
            <Textarea
              id="deny-reason"
              value={reason}
              onChange={(e) => setReason(e.target.value)}
              placeholder={t("reasonPlaceholder")}
              className="min-h-[96px]"
              maxLength={500}
              autoFocus
            />
            <p className="text-right text-[10px] sh-muted">
              {reason.trim().length < MIN_DENY_REASON
                ? t("minHint", { min: MIN_DENY_REASON })
                : `${reason.length} / 500`}
            </p>
          </div>
        ) : null}

        <DialogFooter>
          <Button
            variant="ghost"
            onClick={() => onOpenChange(false)}
            disabled={decide.isPending}
          >
            {tCommon("cancel")}
          </Button>
          <Button
            variant={isDeny ? "destructive" : "default"}
            onClick={submit}
            disabled={!canSubmit}
          >
            {decide.isPending && (
              <IconLoader2 className="size-4 animate-spin" />
            )}
            {isDeny ? t("submit") : tCommon("approve")}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
