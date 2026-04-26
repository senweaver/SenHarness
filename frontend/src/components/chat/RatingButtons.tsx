"use client";

import { useState } from "react";
import {
  IconLoader2,
  IconThumbDown,
  IconThumbUp,
} from "@tabler/icons-react";
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
import { Textarea } from "@/components/ui/textarea";
import {
  useRateMessage,
  useRemoveRating,
} from "@/hooks/use-message-rating";
import { cn } from "@/lib/utils";
import type { MessageRatingSummary, RatingValue } from "@/types/api";

interface RatingButtonsProps {
  sessionId: string;
  messageId: string;
  /** Pre-fetched aggregate, kept fresh by `useSessionRatings`. */
  summary?: MessageRatingSummary;
  /** Disable the buttons before the session is persisted (`/chat/new` race). */
  disabled?: boolean;
  /** Optional className appended to the toolbar wrapper. */
  className?: string;
}

const COMMENT_MAX = 2000;

/**
 * Inline thumbs-up / thumbs-down feedback for an assistant message.
 *
 * UX rules:
 *   - Like is immediate (one click, no dialog).
 *   - Dislike opens a tiny dialog asking for an optional comment, because
 *     the audit log + ops dashboards rely on the "why" to spot prompt regressions.
 *   - Re-clicking the active vote removes it (DELETE).
 */
export function RatingButtons({
  sessionId,
  messageId,
  summary,
  disabled = false,
  className,
}: RatingButtonsProps) {
  const t = useTranslations("chat.rating");
  const rate = useRateMessage();
  const unrate = useRemoveRating();
  const [dialog, setDialog] = useState<{ open: boolean }>({ open: false });
  const [comment, setComment] = useState("");

  const current = summary?.my_rating ?? null;
  const likes = summary?.likes ?? 0;
  const dislikes = summary?.dislikes ?? 0;
  const busy = rate.isPending || unrate.isPending;

  const fire = async (value: RatingValue, withComment?: string | null) => {
    try {
      if (current === value) {
        await unrate.mutateAsync({ sessionId, messageId });
        toast.success(t("removed"));
      } else {
        await rate.mutateAsync({
          sessionId,
          messageId,
          rating: value,
          comment: withComment ?? null,
        });
        toast.success(t("thanks"));
      }
    } catch (err) {
      toast.error((err as Error).message ?? t("failed"));
    }
  };

  const onLike = () => {
    if (disabled || busy) return;
    fire(1);
  };

  const onDislike = () => {
    if (disabled || busy) return;
    if (current === -1) {
      // Unrate (no dialog) — symmetric with the like path.
      fire(-1);
      return;
    }
    setDialog({ open: true });
  };

  const submitDislike = (withComment: boolean) => {
    setDialog({ open: false });
    fire(-1, withComment ? comment.trim() || null : null);
    setComment("");
  };

  return (
    <>
      <div
        className={cn("flex items-center gap-1", className)}
        data-testid="rating-buttons"
      >
        <button
          type="button"
          onClick={onLike}
          disabled={disabled || busy}
          aria-label={current === 1 ? t("unlike") : t("like")}
          title={current === 1 ? t("unlike") : t("like")}
          data-testid="rating-like"
          data-active={current === 1 ? "true" : "false"}
          className={cn(
            "inline-flex h-6 items-center gap-1 rounded-md px-1.5 text-[11px] transition-colors disabled:opacity-50",
            current === 1
              ? "bg-green-500/12 text-green-600 dark:text-green-400"
              : "sh-muted hover:bg-black/5 dark:hover:bg-white/10",
          )}
        >
          {busy && current === 1 ? (
            <IconLoader2 className="size-3.5 animate-spin" />
          ) : (
            <IconThumbUp
              className="size-3.5"
              fill={current === 1 ? "currentColor" : "none"}
            />
          )}
          {likes > 0 && (
            <span className="tabular-nums leading-none">{likes}</span>
          )}
        </button>
        <button
          type="button"
          onClick={onDislike}
          disabled={disabled || busy}
          aria-label={current === -1 ? t("undislike") : t("dislike")}
          title={current === -1 ? t("undislike") : t("dislike")}
          data-testid="rating-dislike"
          data-active={current === -1 ? "true" : "false"}
          className={cn(
            "inline-flex h-6 items-center gap-1 rounded-md px-1.5 text-[11px] transition-colors disabled:opacity-50",
            current === -1
              ? "bg-red-500/12 text-red-600 dark:text-red-400"
              : "sh-muted hover:bg-black/5 dark:hover:bg-white/10",
          )}
        >
          {busy && current === -1 ? (
            <IconLoader2 className="size-3.5 animate-spin" />
          ) : (
            <IconThumbDown
              className="size-3.5"
              fill={current === -1 ? "currentColor" : "none"}
            />
          )}
          {dislikes > 0 && (
            <span className="tabular-nums leading-none">{dislikes}</span>
          )}
        </button>
      </div>

      <Dialog
        open={dialog.open}
        onOpenChange={(o) => setDialog({ open: o })}
      >
        <DialogContent>
          <DialogHeader>
            <DialogTitle>{t("dislikeTitle")}</DialogTitle>
            <DialogDescription>{t("dislikeDescription")}</DialogDescription>
          </DialogHeader>
          <Textarea
            value={comment}
            onChange={(e) => setComment(e.target.value.slice(0, COMMENT_MAX))}
            placeholder={t("commentPlaceholder")}
            className="min-h-[100px]"
            maxLength={COMMENT_MAX}
            autoFocus
          />
          <p className="text-right text-[10px] sh-muted">
            {comment.length} / {COMMENT_MAX}
          </p>
          <DialogFooter>
            <Button
              variant="ghost"
              onClick={() => {
                setDialog({ open: false });
                setComment("");
              }}
            >
              {t("cancel")}
            </Button>
            <Button variant="outline" onClick={() => submitDislike(false)}>
              {t("submitWithoutComment")}
            </Button>
            <Button onClick={() => submitDislike(true)}>
              {t("submitWithComment")}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  );
}
