"use client";

import { useEffect, useState } from "react";
import { useTranslations } from "next-intl";
import { IconInbox } from "@tabler/icons-react";
import { toast } from "sonner";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Sheet,
  SheetContent,
  SheetHeader,
  SheetTitle,
  SheetTrigger,
} from "@/components/ui/sheet";
import { SimpleTooltip } from "@/components/ui/tooltip";
import {
  useCancelPendingMemory,
  useSessionPendingMemories,
} from "@/hooks/use-pending-memories";
import { useMe } from "@/hooks/use-me";
import { useWorkspaceStore } from "@/stores/workspace-store";
import { api } from "@/lib/api";
import type { PendingMemoryRead, SessionRead } from "@/types/api";

interface Props {
  sessionId: string;
}

function formatTimestamp(value: string | null | undefined): string {
  if (!value) return "";
  try {
    return new Date(value).toLocaleString();
  } catch {
    return value;
  }
}

function statusTone(status: PendingMemoryRead["status"]): string {
  switch (status) {
    case "pending":
      return "text-blue-600";
    case "promoted":
      return "text-emerald-600";
    case "skipped":
      return "text-amber-600";
    case "failed":
      return "text-rose-600";
    default:
      return "text-muted-foreground";
  }
}

function payloadSnippet(row: PendingMemoryRead): string {
  const content = (row.payload as { content?: unknown })?.content;
  if (typeof content === "string" && content.length > 0) {
    return content.length > 200 ? `${content.slice(0, 200)}…` : content;
  }
  try {
    return JSON.stringify(row.payload).slice(0, 200);
  } catch {
    return "";
  }
}

function statusLabel(
  status: PendingMemoryRead["status"],
  t: (key: string) => string,
): string {
  switch (status) {
    case "pending":
      return t("statusPending");
    case "promoted":
      return t("statusPromoted");
    case "skipped":
      return t("statusSkipped");
    case "failed":
      return t("statusFailed");
    default:
      return status;
  }
}

export function PendingMemoriesDrawer({ sessionId }: Props) {
  const t = useTranslations("memory");
  const tCommon = useTranslations("common");
  const [open, setOpen] = useState(false);
  const [sessionOwner, setSessionOwner] = useState<string | null | undefined>(
    undefined,
  );
  const { data: me } = useMe();
  const ownsSession = me?.id !== undefined && sessionOwner === me.id;
  const role = useWorkspaceStore((s) => s.workspaces).find(
    (w) => w.id === useWorkspaceStore.getState().activeWorkspaceId,
  )?.role;
  const isAdmin =
    role === "owner" ||
    role === "admin" ||
    me?.platform_role === "platform_admin";
  const canMutate = ownsSession || isAdmin;

  // Keep the badge count live without spamming the API. Refetch every
  // minute while the sheet is closed, every 15s while it's open.
  const query = useSessionPendingMemories(sessionId, {
    enabled: true,
    refetchInterval: open ? 15_000 : 60_000,
  });
  const cancelM = useCancelPendingMemory(sessionId);

  // Ownership lookup only needs to run once per session; defer it until
  // the user actually opens the sheet so closed sessions don't fan out
  // an extra GET on mount.
  useEffect(() => {
    if (!open || sessionOwner !== undefined) return;
    let cancelled = false;
    (async () => {
      try {
        const ses = await api.get<SessionRead>(`/api/v1/sessions/${sessionId}`);
        if (!cancelled) setSessionOwner(ses.owner_identity_id);
      } catch {
        if (!cancelled) setSessionOwner(null);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [open, sessionId, sessionOwner]);

  const rows = query.data ?? [];
  const pendingCount = rows.filter((r) => r.status === "pending").length;

  const onCancel = (row: PendingMemoryRead) => {
    cancelM.mutate(row.id, {
      onSuccess: () => toast.success(t("cancelled")),
    });
  };

  return (
    <Sheet open={open} onOpenChange={setOpen}>
      <SimpleTooltip label={t("pendingDrawerTitle")} side="bottom">
        <SheetTrigger asChild>
          <Button
            variant="ghost"
            size="icon"
            className="relative size-7"
            aria-label={t("pendingDrawerTitle")}
          >
            <IconInbox className="size-3.5" />
            {pendingCount > 0 ? (
              <Badge
                variant="warning"
                className="absolute -right-1 -top-1 h-4 min-w-[1rem] justify-center bg-background px-1 text-[9px] leading-none"
              >
                {pendingCount}
              </Badge>
            ) : null}
          </Button>
        </SheetTrigger>
      </SimpleTooltip>
      <SheetContent side="right" className="w-full max-w-md overflow-y-auto">
        <SheetHeader>
          <SheetTitle>{t("pendingDrawerTitle")}</SheetTitle>
        </SheetHeader>

        {query.isLoading ? (
          <div className="mt-3 text-xs text-muted-foreground">
            {tCommon("loading")}
          </div>
        ) : null}

        {!query.isLoading && rows.length === 0 ? (
          <div className="mt-3 text-xs text-muted-foreground">
            {t("pendingEmpty")}
          </div>
        ) : null}

        {rows.length > 0 ? (
          <ul className="mt-3 space-y-2 text-xs">
            {rows.map((row) => (
              <li
                key={row.id}
                className="rounded-md border bg-background/60 px-3 py-2"
              >
                <div className="flex items-start justify-between gap-2">
                  <div className="min-w-0 flex-1">
                    <div className="flex items-center gap-2">
                      <span
                        className={`text-[10px] font-mono uppercase ${statusTone(row.status)}`}
                      >
                        {statusLabel(row.status, t)}
                      </span>
                      <span className="text-[10px] text-muted-foreground">
                        {formatTimestamp(row.created_at)}
                      </span>
                    </div>
                    <p className="mt-1 break-words text-[11px] text-foreground/80">
                      {payloadSnippet(row)}
                    </p>
                    {row.failure_reason ? (
                      <p className="mt-1 text-[10px] text-rose-600">
                        {row.failure_reason}
                      </p>
                    ) : null}
                  </div>
                  {row.status === "pending" && canMutate ? (
                    <Button
                      variant="ghost"
                      size="sm"
                      onClick={() => onCancel(row)}
                      disabled={cancelM.isPending}
                      className="shrink-0 text-[11px]"
                      title={t("cancelConfirm")}
                    >
                      {t("cancelButton")}
                    </Button>
                  ) : null}
                </div>
              </li>
            ))}
          </ul>
        ) : null}
      </SheetContent>
    </Sheet>
  );
}
