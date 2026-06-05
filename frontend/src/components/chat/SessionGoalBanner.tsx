"use client";

import { useState } from "react";
import { useLocale, useTranslations } from "next-intl";
import {
  IconChevronDown,
  IconChevronUp,
  IconLockOpen,
  IconPencil,
  IconTarget,
} from "@tabler/icons-react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { SessionGoalDialog } from "@/components/chat/SessionGoalDialog";
import {
  useActiveSessionGoal,
  useUnlockGoal,
} from "@/hooks/use-session-goals";
import { cn, relativeTime } from "@/lib/utils";

interface SessionGoalBannerProps {
  sessionId: string;
  /** Optional className for layout (e.g. ``sticky top-0``). */
  className?: string;
}

/**
 * Sticky banner above the chat transcript that shows the **locked** goal,
 * who locked it, the alignment threshold, and exposes edit / unlock
 * actions. Renders ``null`` when no goal is active — the lock entry
 * point lives on the ChatHeader so the chat content column doesn't
 * waste a row on an empty status strip.
 */
export function SessionGoalBanner({
  sessionId,
  className,
}: SessionGoalBannerProps) {
  const t = useTranslations("sessionGoal");
  const locale = useLocale();
  const goalQ = useActiveSessionGoal(sessionId);
  const unlock = useUnlockGoal();
  const [collapsed, setCollapsed] = useState(false);
  const [dialogOpen, setDialogOpen] = useState(false);

  const goal = goalQ.data ?? null;

  const onUnlock = async () => {
    if (!goal) return;
    if (!window.confirm(t("unlockConfirm"))) return;
    try {
      await unlock.mutateAsync({ sessionId, goalId: goal.id });
      toast.success(t("unlockSucceeded"));
    } catch (err) {
      toast.error(t("unlockFailed"), {
        description: (err as Error).message,
      });
    }
  };

  if (!goal) return null;

  return (
    <div
      className={cn(
        "sticky top-0 z-10 mx-auto w-full max-w-3xl border-b bg-background/90 backdrop-blur",
        className,
      )}
    >
      <div className="flex items-center gap-3 px-4 py-2">
        <IconTarget size={16} className="shrink-0 text-primary" />
        <div className="min-w-0 flex-1">
          <div className="truncate text-sm font-medium">{goal.goal_text}</div>
          {!collapsed ? (
            <div className="mt-1 flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-muted-foreground">
              <span>
                {t("thresholdShort", {
                  value: goal.alignment_threshold.toFixed(2),
                })}
              </span>
              <span>{t("lockedAt", { when: relativeTime(goal.locked_at, locale) })}</span>
              {goal.success_criteria.length > 0 ? (
                <ul className="ml-2 list-disc pl-4 text-xs leading-tight">
                  {goal.success_criteria.map((c, i) => (
                    <li key={i}>{c}</li>
                  ))}
                </ul>
              ) : null}
            </div>
          ) : null}
        </div>
        <Button
          variant="ghost"
          size="icon"
          className="h-7 w-7"
          aria-label={collapsed ? t("expand") : t("collapse")}
          onClick={() => setCollapsed((v) => !v)}
        >
          {collapsed ? <IconChevronDown size={14} /> : <IconChevronUp size={14} />}
        </Button>
        <Button
          variant="ghost"
          size="sm"
          className="h-7 px-2 text-xs"
          onClick={() => setDialogOpen(true)}
        >
          <IconPencil size={12} className="mr-1" />
          {t("openEditDialog")}
        </Button>
        <Button
          variant="ghost"
          size="sm"
          className="h-7 px-2 text-xs"
          onClick={onUnlock}
          disabled={unlock.isPending}
        >
          <IconLockOpen size={12} className="mr-1" />
          {t("unlockButton")}
        </Button>
      </div>
      <SessionGoalDialog
        sessionId={sessionId}
        open={dialogOpen}
        onOpenChange={setDialogOpen}
        existing={goal}
      />
    </div>
  );
}
