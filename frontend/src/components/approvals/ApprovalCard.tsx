"use client";

import { useState } from "react";
import {
  IconBuildingCommunity,
  IconCheck,
  IconChevronDown,
  IconClock,
  IconCode,
  IconExternalLink,
  IconFile,
  IconFileText,
  IconHourglass,
  IconLock,
  IconRobot,
  IconTrash,
  IconWriting,
  IconX,
} from "@tabler/icons-react";
import { useTranslations } from "next-intl";

import { DecisionDialog, type DecisionAction } from "./DecisionDialog";
import { SkillDiffPanel } from "@/components/skills/SkillDiffPanel";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { useCountdown } from "@/hooks/use-countdown";
import { usePermissions } from "@/hooks/use-permissions";
import { cn, relativeTime } from "@/lib/utils";
import type { ApprovalRead, ApprovalResourceType } from "@/types/api";

export interface ApprovalCardProps {
  row: ApprovalRead;
  locale: string;
  /** Show the row checkbox + decide buttons (false = read-only / history). */
  decidable?: boolean;
  selected?: boolean;
  onToggleSelect?: () => void;
}

/**
 * `ApprovalCard` — wide-approval card renderer (M2.5).
 *
 * Picks one of seven inner renderers based on
 * ``row.resource_type``:
 *
 * * `skill_pack_create` / `_patch` / `_edit` — embeds
 *   ``SkillDiffPanel`` (M1.10) showing proposed body vs current
 *   ACTIVE, plus judge replay deltas + supporting run ids.
 * * `skill_pack_archive` / `_delete` — pack metadata, last_used_at,
 *   30-day use count, and the rationale.
 * * `skill_pack_write_file` / `_remove_file` — relative path +
 *   content excerpt.
 * * `flow_create` — schedule + prompt template + target agent +
 *   delivery channel ids.
 * * legacy tool-call (``resource_type === null``) — the original
 *   tool / args summary the M0 UI already rendered.
 *
 * Every variant carries the expiry countdown, requester badge, and
 * approve / deny buttons (gated by ``decidable`` + the per-row
 * ``canDecideApproval`` permission).
 */
export function ApprovalCard({
  row,
  locale,
  decidable = true,
  selected = false,
  onToggleSelect,
}: ApprovalCardProps) {
  const t = useTranslations("approvals");
  const tCard = useTranslations("approvalCard");
  const perms = usePermissions();
  const { label: countdownLabel, totalMs, expired } = useCountdown(
    row.expires_at,
  );
  const [dialog, setDialog] = useState<{
    open: boolean;
    action: DecisionAction;
  }>({ open: false, action: "approve" });

  const canDecide =
    decidable &&
    perms.canDecideApproval({
      requestedByIdentityId: row.requested_by_identity_id,
    });

  // Last-60s red, 60-120s amber, otherwise neutral countdown chip.
  const urgency: "red" | "amber" | "neutral" =
    expired || totalMs <= 0
      ? "red"
      : totalMs <= 60_000
        ? "red"
        : totalMs <= 120_000
          ? "amber"
          : "neutral";

  const countdownClass = cn(
    "flex items-center gap-1 font-mono tabular-nums text-xs",
    urgency === "red" && "text-rose-600 dark:text-rose-400",
    urgency === "amber" && "text-amber-600 dark:text-amber-400",
    urgency === "neutral" && "sh-muted",
  );

  const rt = row.resource_type as ApprovalResourceType | null | undefined;

  return (
    <>
      <div
        className={cn(
          "rounded-md border p-3 transition-colors",
          selected
            ? "border-amber-500 bg-amber-100/60 dark:border-amber-500 dark:bg-amber-900/30"
            : "border-amber-300 bg-amber-50/40 dark:border-amber-700 dark:bg-amber-950/20",
        )}
      >
        <div className="flex flex-wrap items-center gap-2">
          {decidable && canDecide && onToggleSelect && (
            <input
              type="checkbox"
              checked={selected}
              onChange={onToggleSelect}
              aria-label={tCard("selectAria", {
                title: cardTitle(rt, t, tCard, row),
              })}
              className="size-3.5 accent-amber-500"
            />
          )}
          <CardKindBadge rt={rt} fallback={row.tool_name} />
          <span className="text-xs font-medium">
            {cardTitle(rt, t, tCard, row)}
          </span>
          <span className="text-[11px] sh-muted">
            {relativeTime(row.created_at, locale)}
          </span>
          {row.requester_department_name && (
            <Badge variant="outline" className="gap-1 text-[10px]">
              <IconBuildingCommunity className="size-3" />
              {row.requester_department_name}
            </Badge>
          )}
          {row.expires_at && (
            <span className={countdownClass} title={row.expires_at}>
              <IconHourglass className="size-3" />
              {expired ? t("expiredLabel") : countdownLabel}
            </span>
          )}
          {row.reminder_sent && !expired && (
            <Badge variant="outline" className="gap-1 text-[10px]">
              <IconClock className="size-3" />
              {tCard("reminderSent")}
            </Badge>
          )}
        </div>

        {row.summary && (
          <div className="mt-2 break-words text-[12px] sh-muted">
            {row.summary}
          </div>
        )}

        <div className="mt-3">
          <ResourceBody row={row} />
        </div>

        <div className="mt-3 flex flex-wrap items-center gap-2">
          {decidable ? (
            canDecide ? (
              <>
                <Button
                  size="sm"
                  className="h-7 bg-emerald-600 hover:bg-emerald-700"
                  onClick={() => setDialog({ open: true, action: "approve" })}
                >
                  <IconCheck className="size-3" />
                  {t("approve")}
                </Button>
                <Button
                  size="sm"
                  variant="destructive"
                  className="h-7"
                  onClick={() => setDialog({ open: true, action: "deny" })}
                >
                  <IconX className="size-3" />
                  {t("deny")}
                </Button>
              </>
            ) : (
              <Badge variant="outline" className="gap-1">
                <IconLock className="size-3" />
                {t("noPermission")}
              </Badge>
            )
          ) : null}
        </div>
      </div>

      <DecisionDialog
        approvalId={row.id}
        action={dialog.action}
        summary={row.summary}
        open={dialog.open}
        onOpenChange={(o) => setDialog((d) => ({ ...d, open: o }))}
      />
    </>
  );
}

// ─── Inner renderer: pick by resource_type ───────────────────
function ResourceBody({ row }: { row: ApprovalRead }) {
  const rt = row.resource_type as ApprovalResourceType | null | undefined;
  switch (rt) {
    case "skill_pack_create":
    case "skill_pack_patch":
    case "skill_pack_edit":
      return <SkillVersionBody row={row} />;
    case "skill_pack_archive":
    case "skill_pack_delete":
      return <SkillArchiveBody row={row} />;
    case "skill_pack_write_file":
    case "skill_pack_remove_file":
      return <SkillFileBody row={row} />;
    case "flow_create":
      return <FlowCreateBody row={row} />;
    default:
      return <LegacyToolBody row={row} />;
  }
}

// ─── Body: skill_pack_create / patch / edit ─────────────────
function SkillVersionBody({ row }: { row: ApprovalRead }) {
  const tCard = useTranslations("approvalCard");
  const args = row.tool_args ?? {};
  const supportingRunIds = (args.supporting_run_ids as string[] | undefined) ?? [];
  const oldExcerpt = (args.old_excerpt as string | undefined) ?? "";
  const newExcerpt = (args.new_excerpt as string | undefined) ?? "";
  const contentExcerpt =
    (args.content_excerpt as string | undefined) ??
    (args.new_text as string | undefined) ??
    "";
  const versionNo = args.version_no as number | undefined;
  const slug = args.slug as string | undefined;
  const validation =
    (args.validation_results as
      | { old_score_avg?: number; new_score_avg?: number; replay_pairs?: number }
      | undefined) ?? null;
  const rationale = args.rationale as string | undefined;

  return (
    <div className="space-y-2">
      {rationale && (
        <div className="rounded border border-amber-200 bg-amber-50/60 p-2 text-[12px] dark:border-amber-800 dark:bg-amber-950/30">
          <strong className="font-semibold">{tCard("rationaleLabel")}:</strong>{" "}
          {rationale}
        </div>
      )}

      {row.resource_type === "skill_pack_patch" ? (
        <SkillDiffPanel
          oldContent={oldExcerpt}
          newContent={newExcerpt}
          fileLabel="SKILL.md"
          fromLabel="current"
          toLabel={`v${versionNo ?? "?"}`}
        />
      ) : (
        <SkillDiffPanel
          oldContent=""
          newContent={contentExcerpt}
          fileLabel="SKILL.md"
          fromLabel={
            row.resource_type === "skill_pack_create" ? "/dev/null" : "current"
          }
          toLabel={`v${versionNo ?? "?"}`}
        />
      )}

      {validation && (
        <JudgeReplayCard
          oldScore={validation.old_score_avg}
          newScore={validation.new_score_avg}
          replayPairs={validation.replay_pairs}
        />
      )}

      {supportingRunIds.length > 0 && (
        <SupportingRunIds ids={supportingRunIds} />
      )}

      {slug && (
        <div className="text-[11px] sh-muted">
          slug=<code className="font-mono">{slug}</code>
          {versionNo ? <> · v{versionNo}</> : null}
        </div>
      )}
    </div>
  );
}

// ─── Body: skill_pack_archive / delete ──────────────────────
function SkillArchiveBody({ row }: { row: ApprovalRead }) {
  const tCard = useTranslations("approvalCard");
  const args = row.tool_args ?? {};
  const slug = args.slug as string | undefined;
  const lastUsedAt = args.last_used_at as string | undefined;
  const useCount30d = args.use_count_30d as number | undefined;
  const currentState = args.current_state as string | undefined;
  const rationale =
    (args.rationale as string | undefined) ??
    (args.reason as string | undefined);

  return (
    <div className="space-y-2 text-[12px]">
      {slug && (
        <div className="flex items-center gap-2">
          <Badge variant="outline" className="font-mono text-[10px]">
            {slug}
          </Badge>
          {currentState && (
            <Badge variant="outline" className="text-[10px]">
              {tCard("stateLabel")}: {currentState}
            </Badge>
          )}
        </div>
      )}
      <dl className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-1 text-[11px] sh-muted">
        {lastUsedAt && (
          <>
            <dt>{tCard("lastUsedAt")}:</dt>
            <dd>{lastUsedAt}</dd>
          </>
        )}
        {typeof useCount30d === "number" && (
          <>
            <dt>{tCard("useCount30d")}:</dt>
            <dd>{useCount30d}</dd>
          </>
        )}
      </dl>
      {rationale && (
        <div className="rounded border border-amber-200 bg-amber-50/60 p-2 dark:border-amber-800 dark:bg-amber-950/30">
          <strong className="font-semibold">{tCard("reasonLabel")}:</strong>{" "}
          {rationale}
        </div>
      )}
    </div>
  );
}

// ─── Body: skill_pack_write_file / remove_file ──────────────
function SkillFileBody({ row }: { row: ApprovalRead }) {
  const tCard = useTranslations("approvalCard");
  const args = row.tool_args ?? {};
  const path = args.relative_path as string | undefined;
  const slug = args.slug as string | undefined;
  const content =
    (args.content_excerpt as string | undefined) ??
    (args.content as string | undefined) ??
    "";
  const isRemove = row.resource_type === "skill_pack_remove_file";
  const rationale = args.rationale as string | undefined;

  return (
    <div className="space-y-2 text-[12px]">
      <div className="flex items-center gap-2">
        {isRemove ? (
          <IconTrash className="size-4 text-rose-500" />
        ) : (
          <IconFile className="size-4 text-emerald-500" />
        )}
        {slug && (
          <Badge variant="outline" className="font-mono text-[10px]">
            {slug}
          </Badge>
        )}
        <code className="font-mono text-[12px]">{path}</code>
      </div>
      {!isRemove && content && (
        <details className="rounded border bg-black/5 dark:bg-white/5">
          <summary className="cursor-pointer px-2 py-1 text-[11px] font-medium">
            {tCard("contentPreview")} ({content.length} chars)
          </summary>
          <pre className="overflow-x-auto whitespace-pre-wrap break-words p-2 text-[11px]">
            {content}
          </pre>
        </details>
      )}
      {rationale && (
        <div className="text-[11px] sh-muted">
          {tCard("rationaleLabel")}: {rationale}
        </div>
      )}
    </div>
  );
}

// ─── Body: flow_create ──────────────────────────────────────
function FlowCreateBody({ row }: { row: ApprovalRead }) {
  const tCard = useTranslations("approvalCard");
  const args = row.tool_args ?? {};
  const name = args.name as string | undefined;
  const schedule = args.schedule as string | undefined;
  const scheduleKind = args.schedule_kind as string | undefined;
  const promptTemplate = args.prompt_template as string | undefined;
  const targetAgentId = args.target_agent_id as string | undefined;
  const deliveryIds = (args.delivery_channel_ids as string[] | undefined) ?? [];
  const rationale = args.rationale as string | undefined;

  return (
    <div className="space-y-2 text-[12px]">
      <div className="flex flex-wrap items-center gap-2">
        {name && <span className="font-medium">{name}</span>}
        {scheduleKind && (
          <Badge variant="outline" className="text-[10px]">
            {tCard("scheduleKind")}: {scheduleKind}
          </Badge>
        )}
        {schedule && (
          <code className="rounded bg-black/5 px-1.5 py-0.5 font-mono text-[11px] dark:bg-white/5">
            {schedule}
          </code>
        )}
      </div>
      <dl className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-1 text-[11px] sh-muted">
        {targetAgentId && (
          <>
            <dt>{tCard("targetAgent")}:</dt>
            <dd>
              <a
                href={`/agents/${targetAgentId}`}
                className="inline-flex items-center gap-1 underline"
                target="_blank"
                rel="noreferrer"
              >
                <IconRobot className="size-3" />
                {targetAgentId.slice(0, 8)}…
                <IconExternalLink className="size-3" />
              </a>
            </dd>
          </>
        )}
        {deliveryIds.length > 0 && (
          <>
            <dt>{tCard("deliveryChannels")}:</dt>
            <dd className="flex flex-wrap gap-1">
              {deliveryIds.map((cid) => (
                <code
                  key={cid}
                  className="rounded bg-black/5 px-1.5 py-0.5 font-mono text-[10px] dark:bg-white/5"
                >
                  {cid.slice(0, 8)}…
                </code>
              ))}
            </dd>
          </>
        )}
      </dl>
      {promptTemplate && (
        <details className="rounded border bg-black/5 dark:bg-white/5">
          <summary className="cursor-pointer px-2 py-1 text-[11px] font-medium">
            {tCard("promptPreview")}
          </summary>
          <pre className="overflow-x-auto whitespace-pre-wrap break-words p-2 text-[11px]">
            {promptTemplate}
          </pre>
        </details>
      )}
      {rationale && (
        <div className="text-[11px] sh-muted">
          {tCard("rationaleLabel")}: {rationale}
        </div>
      )}
      <div className="rounded border border-blue-200 bg-blue-50/60 p-2 text-[11px] dark:border-blue-900 dark:bg-blue-950/30">
        {tCard("flowSecondGateNote")}
      </div>
    </div>
  );
}

// ─── Body: legacy tool-call (M0.x compatibility) ────────────
function LegacyToolBody({ row }: { row: ApprovalRead }) {
  const t = useTranslations("approvals");
  const [expanded, setExpanded] = useState(false);
  return (
    <div className="space-y-2">
      <button
        type="button"
        className="flex items-center gap-1 text-[11px] sh-muted hover:underline"
        onClick={() => setExpanded((e) => !e)}
      >
        <IconChevronDown
          className={cn(
            "size-3 transition-transform",
            expanded && "rotate-180",
          )}
        />
        {expanded ? t("collapseArgs") : t("expandArgs")}
      </button>
      {expanded && (
        <pre className="mt-1 overflow-x-auto rounded bg-black/5 p-2 text-[10.5px] dark:bg-white/5">
          {JSON.stringify(row.tool_args, null, 2)}
        </pre>
      )}
    </div>
  );
}

// ─── Sub-components ─────────────────────────────────────────
function CardKindBadge({
  rt,
  fallback,
}: {
  rt: ApprovalResourceType | null | undefined;
  fallback: string;
}) {
  const tCard = useTranslations("approvalCard");
  const label = rt ? tCard(`badge.${rt}` as Parameters<typeof tCard>[0]) : fallback;
  const icon = (() => {
    switch (rt) {
      case "skill_pack_create":
      case "skill_pack_patch":
      case "skill_pack_edit":
        return <IconCode className="size-3" />;
      case "skill_pack_archive":
      case "skill_pack_delete":
        return <IconTrash className="size-3" />;
      case "skill_pack_write_file":
        return <IconFileText className="size-3" />;
      case "skill_pack_remove_file":
        return <IconTrash className="size-3" />;
      case "flow_create":
        return <IconClock className="size-3" />;
      default:
        return <IconWriting className="size-3" />;
    }
  })();
  return (
    <Badge variant="outline" className="gap-1 font-mono text-[10px]">
      {icon}
      {label}
    </Badge>
  );
}

function JudgeReplayCard({
  oldScore,
  newScore,
  replayPairs,
}: {
  oldScore?: number;
  newScore?: number;
  replayPairs?: number;
}) {
  const tCard = useTranslations("approvalCard");
  if (oldScore === undefined && newScore === undefined) return null;
  const delta =
    typeof oldScore === "number" && typeof newScore === "number"
      ? newScore - oldScore
      : null;
  const deltaCls =
    delta === null
      ? ""
      : delta > 0
        ? "text-emerald-600 dark:text-emerald-400"
        : delta < 0
          ? "text-rose-600 dark:text-rose-400"
          : "sh-muted";
  return (
    <div className="rounded border bg-black/5 p-2 text-[11px] dark:bg-white/5">
      <div className="font-medium">{tCard("judgeReplayTitle")}</div>
      <dl className="mt-1 grid grid-cols-[auto_1fr] gap-x-4 gap-y-0.5 sh-muted">
        <dt>{tCard("judgeOldScore")}:</dt>
        <dd>{typeof oldScore === "number" ? oldScore.toFixed(2) : "—"}</dd>
        <dt>{tCard("judgeNewScore")}:</dt>
        <dd>{typeof newScore === "number" ? newScore.toFixed(2) : "—"}</dd>
        {delta !== null && (
          <>
            <dt>{tCard("judgeDelta")}:</dt>
            <dd className={cn("font-mono", deltaCls)}>
              {delta > 0 ? "+" : ""}
              {delta.toFixed(3)}
            </dd>
          </>
        )}
        {typeof replayPairs === "number" && (
          <>
            <dt>{tCard("judgeReplayPairs")}:</dt>
            <dd>{replayPairs}</dd>
          </>
        )}
      </dl>
    </div>
  );
}

function SupportingRunIds({ ids }: { ids: string[] }) {
  const tCard = useTranslations("approvalCard");
  if (ids.length === 0) return null;
  return (
    <div className="text-[11px] sh-muted">
      <div className="mb-1 font-medium">{tCard("supportingRunIds")}:</div>
      <div className="flex flex-wrap gap-1">
        {ids.slice(0, 8).map((id) => (
          <a
            key={id}
            href={`/runs/${id}`}
            target="_blank"
            rel="noreferrer"
            className="inline-flex items-center gap-1 rounded bg-black/5 px-1.5 py-0.5 font-mono text-[10px] underline dark:bg-white/5"
          >
            {id.slice(0, 8)}…
            <IconExternalLink className="size-3" />
          </a>
        ))}
        {ids.length > 8 && (
          <span className="text-[10px]">+{ids.length - 8}</span>
        )}
      </div>
    </div>
  );
}

function cardTitle(
  rt: ApprovalResourceType | null | undefined,
  t: ReturnType<typeof useTranslations>,
  tCard: ReturnType<typeof useTranslations>,
  row: ApprovalRead,
): string {
  if (!rt) return row.tool_name;
  return tCard(`cardTitle.${rt}` as Parameters<typeof tCard>[0]);
}
