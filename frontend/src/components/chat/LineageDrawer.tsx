"use client";

import { useTranslations } from "next-intl";

import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetHeader,
  SheetTitle,
} from "@/components/ui/sheet";
import { Skeleton } from "@/components/ui/skeleton";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { useLineageReplay } from "@/hooks/use-lineage-replay";

interface LineageDrawerProps {
  sessionId: string;
  messageId: string | null;
  open: boolean;
  onOpenChange: (next: boolean) => void;
}

export function LineageDrawer({
  sessionId,
  messageId,
  open,
  onOpenChange,
}: LineageDrawerProps) {
  const t = useTranslations("lineage");
  const replay = useLineageReplay(sessionId, messageId, { enabled: open });

  return (
    <Sheet open={open} onOpenChange={onOpenChange}>
      <SheetContent
        side="right"
        className="flex w-full flex-col gap-3 overflow-y-auto sm:max-w-lg"
      >
        <SheetHeader>
          <SheetTitle>{t("drawerTitle")}</SheetTitle>
          <SheetDescription>{t("drawerDescription")}</SheetDescription>
        </SheetHeader>

        {replay.isLoading ? (
          <div className="space-y-2">
            <Skeleton className="h-12 w-full" />
            <Skeleton className="h-12 w-full" />
            <Skeleton className="h-12 w-full" />
          </div>
        ) : null}

        {replay.isError ? (
          <div className="rounded border border-amber-500/30 bg-amber-500/10 p-3 text-xs text-amber-700 dark:text-amber-400">
            {(replay.error?.code ?? "") === "lineage.not_compressed"
              ? t("noLineage")
              : t("loadError")}
          </div>
        ) : null}

        {replay.data ? (
          <div className="flex flex-col gap-3">
            <div className="flex flex-wrap items-center gap-2 text-[11px]">
              <Badge variant="primary">
                {t("originalTurnsLabel", {
                  count: replay.data.original_turn_count,
                })}
              </Badge>
              <Badge variant="outline">
                {t("compactionStrategy")}: {replay.data.compaction_strategy}
              </Badge>
              <span className="sh-muted font-mono">
                {t("compressedAt")}:{" "}
                {new Date(replay.data.compressed_at).toLocaleString()}
              </span>
            </div>

            <ol className="relative space-y-2 border-l border-black/10 pl-4 dark:border-white/15">
              {replay.data.original_turns.map((node) => (
                <li key={node.message_id} className="rounded-md border p-3">
                  <div className="mb-1 flex items-center gap-2 text-[11px]">
                    <Badge variant="default">{node.role}</Badge>
                    <span className="sh-muted font-mono">
                      {new Date(node.created_at).toLocaleTimeString()}
                    </span>
                    <span className="sh-muted font-mono opacity-60">
                      {node.message_id.slice(0, 8)}
                    </span>
                  </div>
                  <p className="whitespace-pre-wrap text-[12px] leading-relaxed">
                    {node.text_excerpt || (
                      <span className="sh-muted italic">
                        {t("emptyExcerpt")}
                      </span>
                    )}
                  </p>
                </li>
              ))}
            </ol>

            {replay.data.original_turns.length === 0 ? (
              <div className="rounded border p-4 text-center text-xs sh-muted">
                {t("emptyTurns")}
              </div>
            ) : null}
          </div>
        ) : null}

        <div className="mt-auto flex justify-end">
          <Button
            type="button"
            variant="outline"
            size="sm"
            onClick={() => onOpenChange(false)}
          >
            {t("closeButton")}
          </Button>
        </div>
      </SheetContent>
    </Sheet>
  );
}
