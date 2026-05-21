"use client";

import { useState } from "react";
import { useTranslations } from "next-intl";
import { toast } from "sonner";
import { IconExternalLink, IconLoader2, IconX } from "@tabler/icons-react";

import { Button } from "@/components/ui/button";
import { Sheet, SheetContent, SheetHeader, SheetTitle } from "@/components/ui/sheet";
import { api } from "@/lib/api";
import { Link } from "@/lib/navigation";
import type { RuntimeRunCard } from "@/hooks/use-agent-runtime";
import { useQueryClient } from "@tanstack/react-query";

interface RuntimeDetailDrawerProps {
  card: RuntimeRunCard | null;
  open: boolean;
  onOpenChange: (open: boolean) => void;
}

export function RuntimeDetailDrawer({
  card,
  open,
  onOpenChange,
}: RuntimeDetailDrawerProps) {
  const t = useTranslations("agentView.drawer");
  const qc = useQueryClient();
  const [pending, setPending] = useState<"stop" | "recycle" | null>(null);

  if (!card) {
    return (
      <Sheet open={open} onOpenChange={onOpenChange}>
        <SheetContent>
          <SheetHeader>
            <SheetTitle>{t("title")}</SheetTitle>
          </SheetHeader>
        </SheetContent>
      </Sheet>
    );
  }

  const runAction = async (action: "stop" | "recycle") => {
    setPending(action);
    try {
      await api.post(
        `/api/v1/agent-runtime/runs/${card.run_id}/${action}`,
        {},
      );
      toast.success(t(`${action}Done`));
      qc.invalidateQueries({ queryKey: ["agent-runtime", "snapshot"] });
      onOpenChange(false);
    } catch (err) {
      toast.error(t(`${action}Failed`, { error: (err as Error).message }));
    } finally {
      setPending(null);
    }
  };

  return (
    <Sheet open={open} onOpenChange={onOpenChange}>
      <SheetContent className="flex w-full max-w-md flex-col gap-4 sm:max-w-md">
        <SheetHeader>
          <SheetTitle>{card.agent_name ?? t("title")}</SheetTitle>
        </SheetHeader>
        <dl className="grid grid-cols-2 gap-2 text-xs">
          <dt className="sh-muted">{t("runId")}</dt>
          <dd className="truncate font-mono">{card.run_id}</dd>
          <dt className="sh-muted">{t("sessionId")}</dt>
          <dd className="truncate font-mono">{card.session_id}</dd>
          <dt className="sh-muted">{t("state")}</dt>
          <dd>{card.state}</dd>
          {card.current_phase ? (
            <>
              <dt className="sh-muted">{t("phase")}</dt>
              <dd>{card.current_phase}</dd>
            </>
          ) : null}
          {card.running_tool_name ? (
            <>
              <dt className="sh-muted">{t("tool")}</dt>
              <dd className="font-mono">{card.running_tool_name}</dd>
            </>
          ) : null}
          <dt className="sh-muted">{t("age")}</dt>
          <dd className="tabular-nums">{Math.round(card.age_ms / 1000)}s</dd>
          <dt className="sh-muted">{t("idleMs")}</dt>
          <dd className="tabular-nums">
            {Math.round(card.ms_since_last_event / 1000)}s
          </dd>
          {card.stuck_reason ? (
            <>
              <dt className="sh-muted">{t("stuckReason")}</dt>
              <dd>{card.stuck_reason}</dd>
            </>
          ) : null}
          <dt className="sh-muted">{t("user")}</dt>
          <dd className="truncate">{card.user_name ?? "—"}</dd>
        </dl>

        <div className="mt-auto flex flex-col gap-2">
          <Button asChild variant="outline" size="sm">
            <Link href={`/chat/${card.session_id}`}>
              <IconExternalLink className="mr-1 size-3.5" />
              {t("openSession")}
            </Link>
          </Button>
          <Button
            type="button"
            variant="outline"
            size="sm"
            disabled={pending !== null}
            onClick={() => runAction("recycle")}
          >
            {pending === "recycle" ? (
              <IconLoader2 className="mr-1 size-3.5 animate-spin" />
            ) : null}
            {t("recycle")}
          </Button>
          <Button
            type="button"
            variant="destructive"
            size="sm"
            disabled={pending !== null}
            onClick={() => runAction("stop")}
          >
            {pending === "stop" ? (
              <IconLoader2 className="mr-1 size-3.5 animate-spin" />
            ) : (
              <IconX className="mr-1 size-3.5" />
            )}
            {t("stop")}
          </Button>
        </div>
      </SheetContent>
    </Sheet>
  );
}
