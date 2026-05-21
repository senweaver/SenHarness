"use client";

import { useMemo, useState } from "react";
import { useTranslations } from "next-intl";
import { IconReportAnalytics } from "@tabler/icons-react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetHeader,
  SheetTitle,
  SheetTrigger,
} from "@/components/ui/sheet";
import { SimpleTooltip } from "@/components/ui/tooltip";
import { useSessionArtifacts } from "@/hooks/use-session-artifacts";
import {
  useArtifactVerdict,
  useRejudgeArtifact,
  useSessionJudgeSummary,
} from "@/hooks/use-judge-verdict";
import { useMe } from "@/hooks/use-me";
import { useWorkspaceStore } from "@/stores/workspace-store";

interface Props {
  sessionId: string;
}

const DEBUG_FLAG_ENABLED =
  process.env.NEXT_PUBLIC_DEBUG_ARTIFACTS === "true";

function judgeColor(score: number | null): string {
  if (score === null) return "text-muted-foreground";
  if (score >= 0.5) return "text-emerald-600";
  if (score <= -0.5) return "text-rose-600";
  return "text-amber-600";
}

export function ArtifactDebugPanel({ sessionId }: Props) {
  const { data: me } = useMe();
  const isPlatformAdmin = me?.platform_role === "platform_admin";
  const visible = DEBUG_FLAG_ENABLED || isPlatformAdmin;
  const currentRole = useWorkspaceStore((s) => s.workspaces).find(
    (w) => w.id === useWorkspaceStore.getState().activeWorkspaceId,
  )?.role;
  const isWorkspaceAdmin =
    currentRole === "owner" || currentRole === "admin" || isPlatformAdmin;

  const { data: artifacts, isLoading } = useSessionArtifacts(sessionId, {
    limit: 5,
    enabled: visible,
  });
  const { data: summary } = useSessionJudgeSummary(sessionId, {
    enabled: visible,
  });
  const t = useTranslations("sessionArtifact");
  const tJ = useTranslations("judge");

  const rows = useMemo(() => artifacts ?? [], [artifacts]);

  if (!visible) return null;

  return (
    <Sheet>
      <SimpleTooltip label={t("debugTitle")} side="bottom">
        <SheetTrigger asChild>
          <Button
            variant="ghost"
            size="icon"
            className="relative size-7"
            aria-label={t("debugTitle")}
          >
            <IconReportAnalytics className="size-3.5" />
            {rows.length > 0 ? (
              <Badge
                variant="outline"
                className="absolute -right-1 -top-1 h-4 min-w-[1rem] justify-center bg-background px-1 text-[9px] leading-none"
              >
                {rows.length}
              </Badge>
            ) : null}
          </Button>
        </SheetTrigger>
      </SimpleTooltip>
      <SheetContent side="right" className="w-full max-w-md overflow-y-auto">
        <SheetHeader>
          <SheetTitle>{t("debugTitle")}</SheetTitle>
          {summary ? (
            <SheetDescription>
              {tJ("summarySuccess", { n: summary.success })} ·{" "}
              {tJ("summaryPartial", { n: summary.partial })} ·{" "}
              {tJ("summaryFailure", { n: summary.failure })}
              {summary.degraded > 0
                ? ` · ${tJ("degradedNote", { n: summary.degraded })}`
                : ""}
            </SheetDescription>
          ) : null}
        </SheetHeader>

        {isLoading ? (
          <div className="mt-3 text-xs text-muted-foreground">
            {t("loading")}
          </div>
        ) : null}

        {!isLoading && rows.length === 0 ? (
          <div className="mt-3 text-xs text-muted-foreground">
            {t("noArtifacts")}
          </div>
        ) : null}

        {rows.length > 0 ? (
          <ul className="mt-3 space-y-2 text-xs">
            {rows.map((row) => (
              <ArtifactRow
                key={row.id}
                row={row}
                canRejudge={isWorkspaceAdmin}
              />
            ))}
          </ul>
        ) : null}
      </SheetContent>
    </Sheet>
  );
}

function ArtifactRow({
  row,
  canRejudge,
}: {
  row: import("@/types/api").SessionArtifactRead;
  canRejudge: boolean;
}) {
  const t = useTranslations("sessionArtifact");
  const tJ = useTranslations("judge");
  const [expanded, setExpanded] = useState(false);
  const { data: verdict } = useArtifactVerdict(row.id, { enabled: expanded });
  const rejudge = useRejudgeArtifact();

  return (
    <li className="rounded-md border bg-background p-2">
      <header className="flex flex-wrap items-center gap-x-3 gap-y-1 text-muted-foreground">
        <span className="font-mono text-[11px]">
          {t("runIdLabel")}: {row.run_id.slice(0, 8)}…
        </span>
        <span>
          {t("iterationLabel")}: {row.iteration_count}
        </span>
        <span>
          {t("outcomeLabel")}: {row.final_outcome}
        </span>
        <span className={judgeColor(row.judge_score)}>
          {tJ("scoreLabel")}:{" "}
          {row.judge_score === null ? "—" : row.judge_score.toFixed(2)}
        </span>
        <span>
          {t("alignmentLabel")}:{" "}
          {row.goal_alignment_avg === null
            ? "—"
            : row.goal_alignment_avg.toFixed(2)}
        </span>
      </header>
      {row.invoked_tools.length > 0 && (
        <div className="mt-1 text-muted-foreground">
          {t("toolsLabel")}: {row.invoked_tools.join(", ")}
        </div>
      )}
      <div className="mt-1 flex items-center gap-3">
        <button
          type="button"
          className="text-[11px] text-primary underline-offset-2 hover:underline"
          onClick={() => setExpanded((v) => !v)}
        >
          {expanded ? t("expandTurns") : tJ("rationale")}
        </button>
        {canRejudge && row.judge_score !== null ? (
          <button
            type="button"
            className="text-[11px] text-primary underline-offset-2 hover:underline disabled:opacity-50"
            onClick={async () => {
              await rejudge.mutateAsync(row.id);
            }}
            disabled={rejudge.isPending}
          >
            {rejudge.isPending
              ? tJ("rejudgeQueued")
              : tJ("rejudgeButton")}
          </button>
        ) : null}
      </div>
      {expanded ? (
        <div className="mt-2 space-y-2">
          {verdict ? (
            <div className="rounded bg-muted/40 p-2 text-[11px] leading-snug">
              <div>
                <span className="font-medium">{tJ("rationale")}:</span>{" "}
                {verdict.rationale}
              </div>
              {verdict.process_notes_json.length > 0 ? (
                <div className="mt-1">
                  <span className="font-medium">{tJ("processNotes")}:</span>
                  <ul className="ml-4 list-disc">
                    {verdict.process_notes_json.map((n) => (
                      <li key={n}>{n}</li>
                    ))}
                  </ul>
                </div>
              ) : null}
              <div className="mt-1 text-muted-foreground">
                {verdict.judged_by_model ?? ""} ·{" "}
                {verdict.degraded
                  ? tJ("degradedNote", { n: 1 })
                  : `confidence ${verdict.confidence.toFixed(2)}`}
              </div>
            </div>
          ) : (
            <div className="text-muted-foreground">{tJ("noVerdict")}</div>
          )}
          <details>
            <summary className="cursor-pointer text-muted-foreground">
              {t("expandTurns")}
            </summary>
            <pre className="mt-1 max-h-72 overflow-auto whitespace-pre-wrap rounded bg-muted/40 p-2 text-[11px] leading-snug">
              {JSON.stringify(row.turns_json, null, 2)}
            </pre>
          </details>
        </div>
      ) : null}
    </li>
  );
}
