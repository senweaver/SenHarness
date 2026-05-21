"use client";

import { useEffect, useState } from "react";
import {
  IconAlertTriangle,
  IconCheck,
  IconLoader2,
  IconShieldCheck,
  IconShieldX,
} from "@tabler/icons-react";
import { useTranslations } from "next-intl";
import { toast } from "sonner";

import { Badge } from "@/components/ui/badge";
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
import {
  type BulkDecisionResult,
  useBulkDecideApprovals,
} from "@/hooks/use-approvals";
import type { DecisionAction } from "@/components/approvals/DecisionDialog";

const MIN_DENY_REASON = 3;

/**
 * `BulkDecisionDialog` — batch approve/deny with per-row outcome reporting.
 *
 * Behaviour mirrors the single-row `DecisionDialog`:
 *   * Approve: no reason needed; confirm button fires straight away.
 *   * Deny: forces a reason ≥ 3 chars so the audit row captures *why*.
 *
 * The backend never aborts on the first bad id, so after the mutation we
 * flip the dialog into a *results* view that shows:
 *   * how many rows succeeded (with a ✓ summary + auto-close on all-success)
 *   * which failed, tagged by machine error code (``already_decided``,
 *     ``no_permission``, ``not_found``, ``internal``). Partial failure stays
 *     open so the user sees what happened.
 */
export function BulkDecisionDialog({
  approvalIds,
  action,
  open,
  onOpenChange,
  onDone,
}: {
  approvalIds: string[];
  action: DecisionAction;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** Called once the user acks the results view — the caller should clear selection here. */
  onDone?: (result: BulkDecisionResult) => void;
}) {
  const t = useTranslations("approvals");
  const tBulk = useTranslations("approvals.bulk");
  const tDeny = useTranslations("approvals.denyDialog");
  const bulk = useBulkDecideApprovals();
  const [reason, setReason] = useState("");
  const [result, setResult] = useState<BulkDecisionResult | null>(null);

  useEffect(() => {
    if (!open) {
      setReason("");
      setResult(null);
    }
  }, [open]);

  const isDeny = action === "deny";
  const n = approvalIds.length;
  const canSubmit =
    !bulk.isPending &&
    n > 0 &&
    (!isDeny || reason.trim().length >= MIN_DENY_REASON);

  const submit = async () => {
    try {
      const res = await bulk.mutateAsync({
        approvalIds,
        action,
        reason: isDeny ? reason.trim() : "",
      });
      setResult(res);
      if (res.failed.length === 0) {
        toast.success(
          isDeny
            ? tBulk("toastAllDenied", { n: res.succeeded.length })
            : tBulk("toastAllApproved", { n: res.succeeded.length }),
        );
        onOpenChange(false);
        onDone?.(res);
      } else if (res.succeeded.length > 0) {
        toast.warning(
          tBulk("toastPartial", {
            ok: res.succeeded.length,
            bad: res.failed.length,
          }),
        );
      } else {
        toast.error(tBulk("toastAllFailed", { n: res.failed.length }));
      }
    } catch {
      toast.error(t("toastFailed"));
    }
  };

  const ackResults = () => {
    onOpenChange(false);
    if (result) onDone?.(result);
  };

  // Aggregate failures by error_code so the list stays compact.
  const failureBuckets: Record<string, number> = {};
  if (result) {
    for (const f of result.failed) {
      const k = f.error_code ?? "internal";
      failureBuckets[k] = (failureBuckets[k] ?? 0) + 1;
    }
  }

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
            {result
              ? tBulk("resultsTitle")
              : isDeny
                ? tBulk("denyTitle", { n })
                : tBulk("approveTitle", { n })}
          </DialogTitle>
          <DialogDescription>
            {result
              ? tBulk("resultsDescription")
              : isDeny
                ? tBulk("denyDescription")
                : tBulk("approveDescription")}
          </DialogDescription>
        </DialogHeader>

        {!result && isDeny ? (
          <div className="grid gap-1.5">
            <Label htmlFor="bulk-reason">{tDeny("reasonLabel")}</Label>
            <Textarea
              id="bulk-reason"
              value={reason}
              onChange={(e) => setReason(e.target.value)}
              placeholder={tDeny("reasonPlaceholder")}
              className="min-h-[96px]"
              maxLength={500}
              autoFocus
            />
            <p className="text-right text-[10px] sh-muted">
              {reason.trim().length < MIN_DENY_REASON
                ? tDeny("minHint", { min: MIN_DENY_REASON })
                : `${reason.length} / 500`}
            </p>
          </div>
        ) : null}

        {result ? (
          <div className="space-y-2 rounded-md border bg-black/5 p-3 text-xs dark:bg-white/5">
            <div className="flex items-center gap-2">
              <IconCheck className="size-4 text-emerald-500" />
              <span>
                {tBulk("resultOk", { n: result.succeeded.length })}
              </span>
            </div>
            {result.failed.length > 0 && (
              <>
                <div className="flex items-center gap-2">
                  <IconAlertTriangle className="size-4 text-amber-500" />
                  <span>
                    {tBulk("resultFail", { n: result.failed.length })}
                  </span>
                </div>
                <div className="flex flex-wrap gap-1.5 pl-6">
                  {Object.entries(failureBuckets).map(([code, n]) => (
                    <Badge key={code} variant="outline" className="gap-1">
                      <span className="font-mono">{code}</span>
                      <span>× {n}</span>
                    </Badge>
                  ))}
                </div>
              </>
            )}
          </div>
        ) : null}

        <DialogFooter>
          {result ? (
            <Button onClick={ackResults}>{tBulk("close")}</Button>
          ) : (
            <>
              <Button
                variant="ghost"
                onClick={() => onOpenChange(false)}
                disabled={bulk.isPending}
              >
                {t("cancel")}
              </Button>
              <Button
                variant={isDeny ? "destructive" : "default"}
                onClick={submit}
                disabled={!canSubmit}
              >
                {bulk.isPending && (
                  <IconLoader2 className="size-4 animate-spin" />
                )}
                {isDeny
                  ? tBulk("confirmDeny", { n })
                  : tBulk("confirmApprove", { n })}
              </Button>
            </>
          )}
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
