"use client";

/**
 * SoulPendingQueue — pending SOUL update proposals + decide controls.
 *
 * Backend `GET /api/v1/memory-profiles/me/soul/pending` returns the
 * queue; `POST .../{proposal_id}/decide` accepts or rejects. Decisions
 * are audit-logged by the server so the identity has a record of what
 * was ever accepted into their SOUL.md.
 */

import { useState } from "react";
import { IconCheck, IconX, IconInbox, IconLoader2 } from "@tabler/icons-react";
import { useLocale, useTranslations } from "next-intl";
import { toast } from "sonner";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import {
  type SoulPending,
  useDecideSoul,
  useMySoulPending,
} from "@/hooks/use-memory-profiles";
import { relativeTime } from "@/lib/utils";

export function SoulPendingQueue() {
  const t = useTranslations("settings.soul");
  const locale = useLocale();
  const { data, isLoading } = useMySoulPending();
  const decide = useDecideSoul();

  const [reasons, setReasons] = useState<Record<string, string>>({});
  const [busyId, setBusyId] = useState<string | null>(null);

  const act = async (p: SoulPending, decision: "approve" | "reject") => {
    setBusyId(p.id);
    try {
      await decide.mutateAsync({
        proposalId: p.id,
        decision,
        reason: reasons[p.id] ?? "",
      });
      toast.success(decision === "approve" ? t("approved") : t("rejected"));
      setReasons((r) => {
        const copy = { ...r };
        delete copy[p.id];
        return copy;
      });
    } catch (err) {
      toast.error(err instanceof Error ? err.message : t("saveFailed"));
    } finally {
      setBusyId(null);
    }
  };

  if (isLoading) {
    return <Skeleton className="h-24" />;
  }

  const pending = data ?? [];

  if (pending.length === 0) {
    return (
      <Card>
        <CardContent className="py-8 text-center">
          <IconInbox className="mx-auto size-6 sh-muted" />
          <p className="mt-2 text-sm sh-muted">{t("pendingEmpty")}</p>
        </CardContent>
      </Card>
    );
  }

  return (
    <div className="space-y-2" data-testid="soul-pending-list">
      {pending.map((p) => (
        <Card key={p.id}>
          <CardContent className="space-y-2 py-3">
            <div className="flex items-center gap-2 text-[11px] sh-muted">
              <Badge variant="warning">{t("pendingHeading")}</Badge>
              <span>{t("pendingFromAgent", { when: relativeTime(p.proposed_at, locale) })}</span>
              {p.rationale && (
                <span className="truncate">· {p.rationale}</span>
              )}
            </div>

            <pre className="max-h-[260px] overflow-auto rounded border bg-black/2 p-2 text-[12px] dark:bg-white/5">
              {p.proposed_content}
            </pre>

            <div className="flex items-center gap-2">
              <Input
                value={reasons[p.id] ?? ""}
                onChange={(e) =>
                  setReasons((r) => ({ ...r, [p.id]: e.target.value }))
                }
                placeholder={t("rejectReason")}
                className="flex-1"
              />
              <Button
                size="sm"
                variant="ghost"
                onClick={() => void act(p, "reject")}
                disabled={busyId === p.id}
                data-testid={`soul-reject-${p.id}`}
              >
                {busyId === p.id && decide.isPending ? (
                  <IconLoader2 className="size-3.5 animate-spin" />
                ) : (
                  <IconX className="size-3.5" />
                )}
                {t("reject")}
              </Button>
              <Button
                size="sm"
                onClick={() => void act(p, "approve")}
                disabled={busyId === p.id}
                data-testid={`soul-approve-${p.id}`}
              >
                {busyId === p.id && decide.isPending ? (
                  <IconLoader2 className="size-3.5 animate-spin" />
                ) : (
                  <IconCheck className="size-3.5" />
                )}
                {t("approve")}
              </Button>
            </div>
          </CardContent>
        </Card>
      ))}
    </div>
  );
}
