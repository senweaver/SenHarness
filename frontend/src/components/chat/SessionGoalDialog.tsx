"use client";

import { useEffect, useState } from "react";
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
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { useLockGoal, useUpdateGoal } from "@/hooks/use-session-goals";
import type { SessionGoalRead } from "@/types/api";

interface SessionGoalDialogProps {
  sessionId: string;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** Pass the active goal to switch the dialog into edit mode. */
  existing?: SessionGoalRead | null;
}

const DEFAULT_THRESHOLD = 0.6;

export function SessionGoalDialog({
  sessionId,
  open,
  onOpenChange,
  existing,
}: SessionGoalDialogProps) {
  const t = useTranslations("sessionGoal");
  const tCommon = useTranslations("common");
  const lock = useLockGoal();
  const update = useUpdateGoal();

  const isEdit = Boolean(existing);

  const [goalText, setGoalText] = useState("");
  const [criteriaText, setCriteriaText] = useState("");
  const [threshold, setThreshold] = useState<number>(DEFAULT_THRESHOLD);

  useEffect(() => {
    if (!open) return;
    setGoalText(existing?.goal_text ?? "");
    setCriteriaText((existing?.success_criteria ?? []).join("\n"));
    setThreshold(existing?.alignment_threshold ?? DEFAULT_THRESHOLD);
  }, [open, existing]);

  const submitting = lock.isPending || update.isPending;
  const trimmed = goalText.trim();

  const onSubmit = async () => {
    if (!trimmed) return;
    const successCriteria = criteriaText
      .split(/\r?\n/)
      .map((line) => line.trim())
      .filter(Boolean);
    try {
      if (isEdit && existing) {
        await update.mutateAsync({
          sessionId,
          goalId: existing.id,
          body: {
            goal_text: trimmed,
            success_criteria: successCriteria,
            alignment_threshold: threshold,
          },
        });
        toast.success(t("updateSucceeded"));
      } else {
        await lock.mutateAsync({
          sessionId,
          body: {
            goal_text: trimmed,
            success_criteria: successCriteria,
            alignment_threshold: threshold,
          },
        });
        toast.success(t("lockSucceeded"));
      }
      onOpenChange(false);
    } catch (err) {
      const code = (err as { code?: string }).code;
      const message = (err as Error).message;
      toast.error(
        isEdit ? t("updateFailed") : t("lockFailed"),
        { description: code ? `${code} · ${message}` : message },
      );
    }
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-lg">
        <DialogHeader>
          <DialogTitle>{isEdit ? t("editTitle") : t("lockTitle")}</DialogTitle>
          <DialogDescription className="text-xs">
            {t("slashHint")}
          </DialogDescription>
        </DialogHeader>

        <div className="grid gap-4 py-2">
          <div className="grid gap-1.5">
            <Label htmlFor="session-goal-text">{t("currentGoalLabel")}</Label>
            <Textarea
              id="session-goal-text"
              value={goalText}
              onChange={(e) => setGoalText(e.target.value)}
              placeholder={t("goalPlaceholder")}
              rows={4}
              maxLength={2000}
            />
          </div>

          <div className="grid gap-1.5">
            <Label htmlFor="session-goal-criteria">
              {t("successCriteriaLabel")}
            </Label>
            <Textarea
              id="session-goal-criteria"
              value={criteriaText}
              onChange={(e) => setCriteriaText(e.target.value)}
              rows={3}
              placeholder={t("successCriteriaHint")}
            />
            <p className="text-xs text-muted-foreground">
              {t("successCriteriaHint")}
            </p>
          </div>

          <div className="grid gap-1.5">
            <Label htmlFor="session-goal-threshold">
              {t("alignmentThresholdLabel")}
            </Label>
            <Input
              id="session-goal-threshold"
              type="number"
              min={0}
              max={1}
              step={0.05}
              value={threshold}
              onChange={(e) => {
                const n = Number.parseFloat(e.target.value);
                if (Number.isFinite(n)) {
                  setThreshold(Math.min(1, Math.max(0, n)));
                }
              }}
            />
            <p className="text-xs text-muted-foreground">
              {t("alignmentThresholdHint")}
            </p>
          </div>
        </div>

        <DialogFooter>
          <Button
            variant="outline"
            onClick={() => onOpenChange(false)}
            disabled={submitting}
          >
            {tCommon("cancel")}
          </Button>
          <Button onClick={onSubmit} disabled={!trimmed || submitting}>
            {isEdit ? t("saveButton") : t("lockButton")}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
