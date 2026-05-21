"use client";

import { useState } from "react";
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
import { Label } from "@/components/ui/label";
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetHeader,
  SheetTitle,
} from "@/components/ui/sheet";
import { Skeleton } from "@/components/ui/skeleton";
import { Textarea } from "@/components/ui/textarea";
import { SkillDiffPanel } from "@/components/skills/SkillDiffPanel";
import { SkillStateBadge } from "@/components/skills/SkillStateBadge";
import {
  type SkillPackPersisted,
  type SkillPackState,
  useSkillState,
  useSkillTransitions,
} from "@/hooks/use-skill-lifecycle";
import {
  useActivateSkillVersion,
  useRollbackToVersion,
  useSkillVersions,
} from "@/hooks/use-skill-versions";
import {
  type SkillUsageEventKind,
  type SkillUsageRow,
  useSkillUsage,
  useSkillUsageStats,
  useTriggerSkillRollup,
} from "@/hooks/use-skill-usage";
import { ApiError } from "@/lib/api";
import { cn } from "@/lib/utils";
import type { SkillPackVersionRead, SkillPackVersionState } from "@/types/api";

interface Props {
  pack: SkillPackPersisted | null;
  open: boolean;
  onOpenChange: (v: boolean) => void;
  contentMd?: string;
  contentLoading?: boolean;
}

type Tab = "content" | "state" | "transitions" | "versions" | "usage";

export function SkillDetailDrawer({
  pack,
  open,
  onOpenChange,
  contentMd,
  contentLoading,
}: Props) {
  const t = useTranslations("skillLifecycle.drawer");
  const tStates = useTranslations("skillLifecycle.states");
  const tTimeline = useTranslations("skillLifecycle.timeline");
  const tActor = useTranslations("skillLifecycle.timeline.actor");
  const tVersions = useTranslations("skillVersion");
  const tVersionStates = useTranslations("skillVersion.states");
  const tUsage = useTranslations("skillUsage");
  const [tab, setTab] = useState<Tab>("content");
  const [diffTarget, setDiffTarget] = useState<string | null>(null);
  const [rollbackTarget, setRollbackTarget] = useState<SkillPackVersionRead | null>(
    null,
  );
  const [rollbackReason, setRollbackReason] = useState("");

  const stateQuery = useSkillState(open && pack ? pack.id : undefined);
  const transitionsQuery = useSkillTransitions(open && pack ? pack.id : undefined);
  const versionsQuery = useSkillVersions(open && pack ? pack.id : undefined);
  const activate = useActivateSkillVersion(pack?.id ?? "");
  const rollback = useRollbackToVersion(pack?.id ?? "");
  const usageQuery = useSkillUsage(
    open && pack && tab === "usage" ? pack.id : undefined,
    { limit: 20 },
  );
  const usageStatsQuery = useSkillUsageStats(
    open && pack && tab === "usage" ? pack.id : undefined,
  );
  const triggerRollup = useTriggerSkillRollup();

  return (
    <Sheet open={open} onOpenChange={onOpenChange}>
      <SheetContent side="right" className="sm:max-w-xl">
        <SheetHeader>
          <SheetTitle className="flex items-center gap-2">
            {pack?.name ?? t("title")}
          </SheetTitle>
          {pack && (
            <SheetDescription>
              <span className="flex items-center gap-2">
                <SkillStateBadge state={pack.state} pinned={pack.pinned} />
                <code className="text-[11px] sh-muted">{pack.slug}</code>
              </span>
            </SheetDescription>
          )}
        </SheetHeader>

        <nav className="flex gap-2 border-b">
          {(
            ["content", "state", "transitions", "versions", "usage"] as const
          ).map((k) => (
            <button
              key={k}
              type="button"
              className={cn(
                "border-b-2 px-3 py-1.5 text-xs font-medium transition-colors",
                tab === k
                  ? "border-current"
                  : "border-transparent sh-muted hover:opacity-80",
              )}
              onClick={() => setTab(k)}
            >
              {k === "versions"
                ? tVersions("tabTitle")
                : k === "usage"
                  ? tUsage("tabTitle")
                  : t(
                      k === "content"
                        ? "tabContent"
                        : k === "state"
                          ? "tabState"
                          : "tabTransitions",
                    )}
            </button>
          ))}
        </nav>

        <div className="flex-1 overflow-y-auto">
          {tab === "content" && (
            <div className="space-y-2 text-xs">
              {contentLoading && <Skeleton className="h-48" />}
              {!contentLoading && contentMd && (
                <pre className="max-h-[60vh] overflow-y-auto whitespace-pre-wrap break-words rounded-md bg-black/5 p-3 font-mono text-[12px] dark:bg-white/5">
                  {contentMd}
                </pre>
              )}
              {!contentLoading && !contentMd && (
                <p className="py-6 text-center text-sm sh-muted">—</p>
              )}
            </div>
          )}

          {tab === "state" && (
            <div className="space-y-3 text-xs">
              {stateQuery.isLoading && <Skeleton className="h-32" />}
              {stateQuery.data && (
                <>
                  <div className="flex items-center gap-2">
                    <SkillStateBadge
                      state={stateQuery.data.state}
                      pinned={stateQuery.data.pinned}
                    />
                  </div>
                  <Row
                    label={t("stateChangedAt")}
                    value={
                      stateQuery.data.state_changed_at
                        ? new Date(stateQuery.data.state_changed_at).toLocaleString()
                        : t("neverChanged")
                    }
                  />
                  <Row
                    label={t("stateChangedBy")}
                    value={stateQuery.data.state_changed_by ?? "—"}
                  />
                  {stateQuery.data.last_transition && (
                    <TransitionRow
                      entry={stateQuery.data.last_transition}
                      tStates={tStates}
                      tTimeline={tTimeline}
                      tActor={tActor}
                    />
                  )}
                </>
              )}
            </div>
          )}

          {tab === "transitions" && (
            <div className="space-y-2">
              {transitionsQuery.isLoading && <Skeleton className="h-48" />}
              {transitionsQuery.data &&
                transitionsQuery.data.items.length === 0 && (
                  <p className="py-6 text-center text-sm sh-muted">
                    {t("transitionsEmpty")}
                  </p>
                )}
              {transitionsQuery.data?.items.map((entry, i) => (
                <TransitionRow
                  key={`${entry.occurred_at}-${i}`}
                  entry={entry}
                  tStates={tStates}
                  tTimeline={tTimeline}
                  tActor={tActor}
                />
              ))}
            </div>
          )}

          {tab === "versions" && pack && (
            <div className="space-y-2">
              {versionsQuery.isLoading && <Skeleton className="h-48" />}
              {versionsQuery.data && versionsQuery.data.items.length === 0 && (
                <p className="py-6 text-center text-sm sh-muted">
                  {tVersions("noVersions")}
                </p>
              )}
              {versionsQuery.data?.items.map((v) => (
                <VersionRow
                  key={v.id}
                  version={v}
                  packId={pack.id}
                  isPending={activate.isPending}
                  isRollbackPending={
                    rollback.isPending && rollbackTarget?.id === v.id
                  }
                  onActivate={async (versionId) => {
                    try {
                      await activate.mutateAsync({
                        versionId,
                        reason: "user activated",
                      });
                      toast.success(tVersions("activated"));
                    } catch (e) {
                      const msg =
                        e instanceof ApiError
                          ? e.message
                          : tVersions("activateFailed");
                      toast.error(msg);
                    }
                  }}
                  onRollbackRequest={(version) => {
                    setRollbackTarget(version);
                    setRollbackReason("");
                  }}
                  onShowDiff={(versionId) =>
                    setDiffTarget(versionId === diffTarget ? null : versionId)
                  }
                  isDiffOpen={diffTarget === v.id}
                  tVersions={tVersions}
                  tStates={tVersionStates}
                />
              ))}
              {diffTarget && pack && (
                <div className="mt-3">
                  <SkillDiffPanel
                    packId={pack.id}
                    versionA={diffTarget}
                    versionB="active"
                    fileLabel={`${pack.slug}/SKILL.md`}
                  />
                </div>
              )}
            </div>
          )}

          {tab === "usage" && pack && (
            <div className="space-y-3 text-xs">
              {(usageQuery.isLoading || usageStatsQuery.isLoading) && (
                <Skeleton className="h-32" />
              )}
              {usageStatsQuery.data && (
                <div className="space-y-1">
                  <Row
                    label={tUsage("useCountLabel")}
                    value={String(usageStatsQuery.data.use_count)}
                  />
                  <Row
                    label={tUsage("lastUsedLabel")}
                    value={
                      usageStatsQuery.data.last_used_at
                        ? new Date(usageStatsQuery.data.last_used_at).toLocaleString()
                        : "—"
                    }
                  />
                  <Row
                    label={tUsage("effectivenessLabel")}
                    value={
                      usageStatsQuery.data.contribution_avg !== null
                        ? usageStatsQuery.data.contribution_avg.toFixed(2)
                        : "—"
                    }
                  />
                  {Object.entries(usageStatsQuery.data.use_count_by_kind).map(
                    ([kind, count]) => (
                      <Row
                        key={kind}
                        label={tUsage(usageKindKey(kind as SkillUsageEventKind))}
                        value={String(count)}
                      />
                    ),
                  )}
                </div>
              )}
              <div className="flex items-center justify-between">
                <span className="font-medium">{tUsage("recentUsage")}</span>
                <Button
                  variant="outline"
                  size="sm"
                  disabled={triggerRollup.isPending}
                  onClick={async () => {
                    try {
                      await triggerRollup.mutateAsync({ packId: pack.id });
                      toast.success(tUsage("refreshedToast"));
                    } catch (e) {
                      const msg =
                        e instanceof ApiError
                          ? e.message
                          : tUsage("refreshFailedToast");
                      toast.error(msg);
                    }
                  }}
                >
                  {tUsage("refreshButton")}
                </Button>
              </div>
              {usageQuery.data && usageQuery.data.items.length === 0 && (
                <p className="py-6 text-center text-sm sh-muted">
                  {tUsage("noUsage")}
                </p>
              )}
              {usageQuery.data?.items.map((row) => (
                <UsageRow
                  key={row.id}
                  row={row}
                  tUsage={tUsage}
                />
              ))}
            </div>
          )}
        </div>

        <div className="mt-auto flex justify-end pt-2">
          <Button variant="outline" size="sm" onClick={() => onOpenChange(false)}>
            OK
          </Button>
        </div>
      </SheetContent>

      <Dialog
        open={rollbackTarget !== null}
        onOpenChange={(v) => {
          if (!v) {
            setRollbackTarget(null);
            setRollbackReason("");
          }
        }}
      >
        <DialogContent>
          <DialogHeader>
            <DialogTitle>{tVersions("rollbackDialogTitle")}</DialogTitle>
            <DialogDescription>
              {rollbackTarget
                ? tVersions("rollbackDialogBody", {
                    version_no: rollbackTarget.version_no,
                  })
                : ""}
            </DialogDescription>
          </DialogHeader>
          <div className="space-y-2">
            <Label htmlFor="rollback-reason">
              {tVersions("rollbackReasonLabel")}
            </Label>
            <Textarea
              id="rollback-reason"
              value={rollbackReason}
              onChange={(e) => setRollbackReason(e.target.value)}
              maxLength={400}
              rows={3}
            />
          </div>
          <DialogFooter>
            <Button
              variant="outline"
              size="sm"
              onClick={() => {
                setRollbackTarget(null);
                setRollbackReason("");
              }}
            >
              {tVersions("rollbackCancelButton")}
            </Button>
            <Button
              size="sm"
              disabled={rollback.isPending || rollbackReason.trim().length === 0}
              onClick={async () => {
                if (!rollbackTarget) return;
                try {
                  await rollback.mutateAsync({
                    versionId: rollbackTarget.id,
                    reason: rollbackReason.trim(),
                  });
                  toast.success(tVersions("rollbackSuccessToast"));
                  setRollbackTarget(null);
                  setRollbackReason("");
                } catch (e) {
                  const msg =
                    e instanceof ApiError
                      ? e.message
                      : tVersions("rollbackFailedToast");
                  toast.error(msg);
                }
              }}
            >
              {tVersions("rollbackConfirmButton")}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </Sheet>
  );
}

function Row({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex justify-between gap-3">
      <span className="sh-muted">{label}</span>
      <span className="font-mono">{value}</span>
    </div>
  );
}

interface TimelineRowProps {
  entry: {
    from_state: SkillPackState | null;
    to_state: SkillPackState | null;
    reason: string | null;
    actor_kind: string | null;
    occurred_at: string;
  };
  tStates: (k: string) => string;
  tTimeline: (k: string) => string;
  tActor: (k: string) => string;
}

function TransitionRow({ entry, tStates, tTimeline, tActor }: TimelineRowProps) {
  const actorKey =
    entry.actor_kind && ["user", "curator", "system", "evolver"].includes(entry.actor_kind)
      ? entry.actor_kind
      : "user";
  return (
    <div className="rounded border p-2 text-xs">
      <div className="flex items-center gap-1">
        <span className="sh-muted">{tTimeline("fromState")}:</span>
        <code>{entry.from_state ? tStates(entry.from_state) : "—"}</code>
        <span className="sh-muted">→</span>
        <code>{entry.to_state ? tStates(entry.to_state) : "—"}</code>
      </div>
      {entry.reason && (
        <div className="mt-1 sh-muted">
          {tTimeline("reason")}: <span className="text-[rgb(var(--color-fg))]">{entry.reason}</span>
        </div>
      )}
      <div className="mt-1 flex justify-between sh-muted">
        <span>{tActor(actorKey)}</span>
        <time>{new Date(entry.occurred_at).toLocaleString()}</time>
      </div>
    </div>
  );
}

interface VersionRowProps {
  version: SkillPackVersionRead;
  packId: string;
  isPending: boolean;
  isRollbackPending: boolean;
  isDiffOpen: boolean;
  onActivate: (versionId: string) => void;
  onRollbackRequest: (version: SkillPackVersionRead) => void;
  onShowDiff: (versionId: string) => void;
  tVersions: (k: string) => string;
  tStates: (k: SkillPackVersionState) => string;
}

function VersionRow({
  version,
  isPending,
  isRollbackPending,
  isDiffOpen,
  onActivate,
  onRollbackRequest,
  onShowDiff,
  tVersions,
  tStates,
}: VersionRowProps) {
  const isActive = version.state === "active";
  // Once a version is REJECTED the service refuses to revive it; hide
  // the verb so the user doesn't get a 409 toast they can't recover
  // from in the UI.
  const canRollback = !isActive && version.state !== "rejected";
  return (
    <div className="rounded border p-2 text-xs">
      <div className="flex items-center justify-between gap-2">
        <div className="flex items-center gap-2">
          <span className="font-mono">v{version.version_no}</span>
          <span
            className={cn(
              "rounded px-1.5 py-0.5 text-[10px]",
              isActive
                ? "bg-emerald-500/20 text-emerald-700 dark:text-emerald-300"
                : "bg-muted/40 sh-muted",
            )}
          >
            {tStates(version.state)}
          </span>
          {isActive && (
            <span className="text-[10px] text-emerald-700 dark:text-emerald-300">
              {tVersions("currentVersionBadge")}
            </span>
          )}
        </div>
        <time className="sh-muted text-[10px]">
          {new Date(version.created_at).toLocaleString()}
        </time>
      </div>
      <div className="mt-1 flex items-center gap-2 sh-muted text-[10px]">
        <span>{tVersions("createdBy")}: {version.created_by}</span>
        <span className="ml-auto font-mono">
          {version.content_hash.slice(0, 12)}…
        </span>
      </div>
      <div className="mt-2 flex flex-wrap gap-2">
        {!isActive && (
          <Button
            variant="outline"
            size="sm"
            disabled={isPending}
            onClick={() => onActivate(version.id)}
          >
            {tVersions("activateButton")}
          </Button>
        )}
        {canRollback && (
          <Button
            variant="outline"
            size="sm"
            disabled={isRollbackPending}
            onClick={() => onRollbackRequest(version)}
          >
            {tVersions("rollbackButton")}
          </Button>
        )}
        {!isActive && (
          <Button
            variant="ghost"
            size="sm"
            onClick={() => onShowDiff(version.id)}
          >
            {isDiffOpen
              ? tVersions("hideDiffButton")
              : tVersions("showDiffButton")}
          </Button>
        )}
      </div>
    </div>
  );
}


function usageKindKey(kind: SkillUsageEventKind): string {
  switch (kind) {
    case "injected":
      return "kindInjected";
    case "read_full":
      return "kindReadFull";
    case "used_in_tool":
      return "kindUsedInTool";
    case "patched":
      return "kindPatched";
    case "dropped_at_cap":
      return "kindDroppedAtCap";
  }
}


function UsageRow({
  row,
  tUsage,
}: {
  row: SkillUsageRow;
  tUsage: (k: string) => string;
}) {
  return (
    <div className="rounded border p-2 text-xs">
      <div className="flex items-center justify-between gap-2">
        <span className="rounded bg-muted/40 px-1.5 py-0.5 text-[10px] uppercase">
          {tUsage(usageKindKey(row.event_kind))}
        </span>
        <time className="sh-muted text-[10px]">
          {new Date(row.created_at).toLocaleString()}
        </time>
      </div>
      <div className="mt-1 flex gap-2 sh-muted text-[10px]">
        <span className="font-mono">{row.run_id.slice(0, 8)}…</span>
        {row.contribution_score !== null && (
          <span className="ml-auto font-mono">
            {row.contribution_score.toFixed(2)}
          </span>
        )}
      </div>
    </div>
  );
}
