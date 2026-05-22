"use client";

/**
 * Workspace Skill Curator settings (M1.9).
 *
 * Two cards:
 *
 * 1. **Schedule and thresholds** — admin tunes
 *    enabled / stale_after_days / archive_after_days / min_idle_hours /
 *    active_skills_soft_cap. Each knob shows a `source` badge so the
 *    admin can see at a glance whether a knob is workspace-overridden
 *    or falling through to the platform default. Non-admins see the
 *    same view but the inputs are read-only.
 * 2. **Last run** — most recent curator_tick stats sourced from
 *    `audit_events.curator.swept` plus a "Force run now" button (admin
 *    only, rate-limited 2 / 5 min).
 *
 * The page never fetches when there's no active workspace; the layout
 * already redirects unauthenticated users.
 */

import { useEffect, useMemo, useState } from "react";

import {
    IconActivity,
    IconExternalLink,
    IconLoader2,
    IconPlayerPlay,
    IconRotate,
    IconSparkles,
} from "@tabler/icons-react";
import { useTranslations } from "next-intl";
import { toast } from "sonner";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
    Card,
    CardContent,
    CardDescription,
    CardHeader,
    CardTitle,
} from "@/components/ui/card";
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
import { PageHeader } from "@/components/ui/page-header";
import { Skeleton } from "@/components/ui/skeleton";
import { Switch } from "@/components/ui/switch";
import {
    CURATOR_FIELD_RANGES,
    CuratorConfigPatch,
    CuratorFieldSource,
    useCuratorConfig,
    useCuratorLastRun,
    useForceCuratorRun,
    useUpdateCuratorConfig,
} from "@/hooks/use-curator-config";
import { useMe } from "@/hooks/use-me";
import { Link } from "@/lib/navigation";
import { cn } from "@/lib/utils";
import { useWorkspaceStore } from "@/stores/workspace-store";

const ADMIN_ROLES = new Set(["owner", "admin"]);

interface DraftConfig {
    enabled: boolean;
    stale_after_days: number;
    archive_after_days: number;
    min_idle_hours: number;
    active_skills_soft_cap: number;
}

export default function WorkspaceSkillsCuratorPage() {
    const t = useTranslations("curatorSettings");
    const tSettings = useTranslations("settings");
    const { data: me, isLoading: meLoading } = useMe();
    const workspaceId = useWorkspaceStore((s) => s.activeWorkspaceId);

    const isAdmin = ADMIN_ROLES.has(me?.current_role ?? "");

    const { data: config, isLoading: configLoading } =
        useCuratorConfig(workspaceId);
    const { data: lastRun, isLoading: lastRunLoading } =
        useCuratorLastRun(workspaceId);
    const update = useUpdateCuratorConfig(workspaceId);
    const runNow = useForceCuratorRun(workspaceId);

    const [draft, setDraft] = useState<DraftConfig | null>(null);
    const [confirmOpen, setConfirmOpen] = useState(false);

    useEffect(() => {
        if (config) {
            setDraft({
                enabled: config.enabled,
                stale_after_days: config.stale_after_days,
                archive_after_days: config.archive_after_days,
                min_idle_hours: config.min_idle_hours,
                active_skills_soft_cap: config.active_skills_soft_cap,
            });
        }
    }, [config]);

    const dirtyFields = useMemo(() => {
        if (!draft || !config) return new Set<keyof DraftConfig>();
        const out = new Set<keyof DraftConfig>();
        (Object.keys(draft) as (keyof DraftConfig)[]).forEach((k) => {
            if (draft[k] !== config[k]) out.add(k);
        });
        return out;
    }, [draft, config]);

    const validationError = useMemo(() => {
        if (!draft) return null;
        if (draft.stale_after_days > draft.archive_after_days) {
            return t("validationErrors.staleGreaterThanArchive");
        }
        return null;
    }, [draft, t]);

    if (meLoading || configLoading || !config || !draft) {
        return (
            <div data-testid="curator-settings-page">
                <PageHeader
                    title={t("pageTitle")}
                    description={t("pageDescription")}
                />
                <Skeleton className="h-32" />
            </div>
        );
    }

    const onSave = async () => {
        if (validationError) {
            toast.error(validationError);
            return;
        }
        const patch: CuratorConfigPatch = {};
        dirtyFields.forEach((k) => {
            (patch as Record<keyof DraftConfig, unknown>)[k] = draft[k];
        });
        if (Object.keys(patch).length === 0) {
            return;
        }
        try {
            await update.mutateAsync(patch);
            toast.success(t("savedToast"));
        } catch {
            toast.error(tSettings("saveFailed"));
        }
    };

    const onResetField = async (field: keyof DraftConfig) => {
        if (config.source[field] !== "workspace") return;
        try {
            await update.mutateAsync({ [field]: null } as CuratorConfigPatch);
            toast.success(t("resetToast"));
        } catch {
            toast.error(tSettings("saveFailed"));
        }
    };

    const onRunNow = async () => {
        setConfirmOpen(false);
        try {
            await runNow.mutateAsync();
            toast.success(t("forceRunSuccessToast"));
        } catch {
            toast.error(t("forceRunFailedToast"));
        }
    };

    return (
        <div data-testid="curator-settings-page">
            <PageHeader
                title={t("pageTitle")}
                description={t("pageDescription")}
            />

            <Card className="mb-4">
                <CardHeader>
                    <CardTitle className="flex items-center gap-2 text-base">
                        <IconSparkles className="size-4" />
                        {t("scheduleCardTitle")}
                    </CardTitle>
                    <CardDescription>
                        {t("scheduleCardDescription")}
                    </CardDescription>
                </CardHeader>
                <CardContent className="space-y-5">
                    <div className="flex items-center justify-between gap-4">
                        <div>
                            <Label className="text-sm font-medium">
                                {t("enabledLabel")}
                            </Label>
                            <p className="text-xs sh-muted">
                                {t("enabledDescription")}
                            </p>
                        </div>
                        <div className="flex items-center gap-2">
                            <SourceBadge source={config.source.enabled} />
                            <Switch
                                checked={draft.enabled}
                                onCheckedChange={(v) =>
                                    setDraft({ ...draft, enabled: v })
                                }
                                disabled={!isAdmin || update.isPending}
                                data-testid="curator-enabled-toggle"
                            />
                        </div>
                    </div>

                    <NumericField
                        label={t("staleAfterDaysLabel")}
                        description={t("staleAfterDaysDescription")}
                        field="stale_after_days"
                        value={draft.stale_after_days}
                        onChange={(v) =>
                            setDraft({ ...draft, stale_after_days: v })
                        }
                        min={CURATOR_FIELD_RANGES.stale_after_days.min}
                        max={CURATOR_FIELD_RANGES.stale_after_days.max}
                        source={config.source.stale_after_days}
                        showSlider
                        readOnly={!isAdmin}
                        onResetField={() => onResetField("stale_after_days")}
                        resetLabel={t("resetToDefaultButton")}
                        showReset={
                            isAdmin &&
                            config.source.stale_after_days === "workspace"
                        }
                        sourceWorkspaceLabel={t("sourceWorkspace")}
                        sourcePlatformLabel={t("sourcePlatformDefault")}
                    />

                    <NumericField
                        label={t("archiveAfterDaysLabel")}
                        description={t("archiveAfterDaysDescription")}
                        field="archive_after_days"
                        value={draft.archive_after_days}
                        onChange={(v) =>
                            setDraft({ ...draft, archive_after_days: v })
                        }
                        min={CURATOR_FIELD_RANGES.archive_after_days.min}
                        max={CURATOR_FIELD_RANGES.archive_after_days.max}
                        source={config.source.archive_after_days}
                        showSlider
                        readOnly={!isAdmin}
                        onResetField={() => onResetField("archive_after_days")}
                        resetLabel={t("resetToDefaultButton")}
                        showReset={
                            isAdmin &&
                            config.source.archive_after_days === "workspace"
                        }
                        sourceWorkspaceLabel={t("sourceWorkspace")}
                        sourcePlatformLabel={t("sourcePlatformDefault")}
                    />

                    <NumericField
                        label={t("minIdleHoursLabel")}
                        description={t("minIdleHoursDescription")}
                        field="min_idle_hours"
                        value={draft.min_idle_hours}
                        onChange={(v) =>
                            setDraft({ ...draft, min_idle_hours: v })
                        }
                        min={CURATOR_FIELD_RANGES.min_idle_hours.min}
                        max={CURATOR_FIELD_RANGES.min_idle_hours.max}
                        source={config.source.min_idle_hours}
                        showSlider
                        readOnly={!isAdmin}
                        onResetField={() => onResetField("min_idle_hours")}
                        resetLabel={t("resetToDefaultButton")}
                        showReset={
                            isAdmin &&
                            config.source.min_idle_hours === "workspace"
                        }
                        sourceWorkspaceLabel={t("sourceWorkspace")}
                        sourcePlatformLabel={t("sourcePlatformDefault")}
                    />

                    <NumericField
                        label={t("activeSkillsSoftCapLabel")}
                        description={t("activeSkillsSoftCapDescription")}
                        field="active_skills_soft_cap"
                        value={draft.active_skills_soft_cap}
                        onChange={(v) =>
                            setDraft({
                                ...draft,
                                active_skills_soft_cap: v,
                            })
                        }
                        min={CURATOR_FIELD_RANGES.active_skills_soft_cap.min}
                        max={CURATOR_FIELD_RANGES.active_skills_soft_cap.max}
                        source={config.source.active_skills_soft_cap}
                        readOnly={!isAdmin}
                        onResetField={() =>
                            onResetField("active_skills_soft_cap")
                        }
                        resetLabel={t("resetToDefaultButton")}
                        showReset={
                            isAdmin &&
                            config.source.active_skills_soft_cap === "workspace"
                        }
                        sourceWorkspaceLabel={t("sourceWorkspace")}
                        sourcePlatformLabel={t("sourcePlatformDefault")}
                    />

                    {validationError && (
                        <p
                            className="text-xs text-red-600 dark:text-red-400"
                            data-testid="curator-validation-error"
                        >
                            {validationError}
                        </p>
                    )}

                    {isAdmin && (
                        <div className="flex justify-end gap-2 pt-2">
                            <Button
                                size="sm"
                                onClick={onSave}
                                disabled={
                                    dirtyFields.size === 0 ||
                                    Boolean(validationError) ||
                                    update.isPending
                                }
                                data-testid="curator-save-button"
                            >
                                {update.isPending ? (
                                    <IconLoader2 className="size-4 animate-spin" />
                                ) : null}
                                {t("saveButton")}
                            </Button>
                        </div>
                    )}
                </CardContent>
            </Card>

            <Card>
                <CardHeader>
                    <CardTitle className="flex items-center gap-2 text-base">
                        <IconActivity className="size-4" />
                        {t("lastRunTitle")}
                    </CardTitle>
                    <CardDescription>
                        {t("lastRunDescription")}
                    </CardDescription>
                </CardHeader>
                <CardContent className="space-y-3">
                    {lastRunLoading ? (
                        <Skeleton className="h-20" />
                    ) : !lastRun?.last_run_at || !lastRun.last_result ? (
                        <p className="text-sm sh-muted">
                            {t("lastRunNever")}
                        </p>
                    ) : (
                        <div
                            className="grid grid-cols-2 gap-3 sm:grid-cols-4"
                            data-testid="curator-last-run-stats"
                        >
                            <Stat
                                label={t("lastRunAt")}
                                value={new Date(
                                    lastRun.last_run_at,
                                ).toLocaleString()}
                            />
                            <Stat
                                label={t("staleProposed")}
                                value={String(
                                    lastRun.last_result.stale_proposed,
                                )}
                            />
                            <Stat
                                label={t("archiveProposed")}
                                value={String(
                                    lastRun.last_result.archive_proposed,
                                )}
                            />
                            <Stat
                                label={t("pinnedSkipped")}
                                value={String(
                                    lastRun.last_result.pinned_skipped,
                                )}
                            />
                            <Stat
                                label={t("durationMs")}
                                value={`${lastRun.last_result.duration_ms} ms`}
                            />
                        </div>
                    )}

                    <div className="flex flex-col gap-2 pt-1 text-xs sh-muted sm:flex-row sm:items-center sm:justify-between">
                        <span>
                            {lastRun?.upcoming_run_at
                                ? t("nextRunAt", {
                                      time: new Date(
                                          lastRun.upcoming_run_at,
                                      ).toLocaleString(),
                                  })
                                : t("nextRunUnknown")}
                        </span>
                        <Link
                            href="/approvals?resource_type=skill_pack_archive"
                            className="inline-flex items-center gap-1 text-xs underline"
                        >
                            <IconExternalLink className="size-3" />
                            {t("viewProposalsLink")}
                        </Link>
                    </div>

                    {isAdmin && (
                        <div className="flex justify-end pt-2">
                            <Button
                                size="sm"
                                variant="outline"
                                onClick={() => setConfirmOpen(true)}
                                disabled={runNow.isPending}
                                data-testid="curator-force-run-button"
                            >
                                {runNow.isPending ? (
                                    <IconLoader2 className="size-4 animate-spin" />
                                ) : (
                                    <IconPlayerPlay className="size-4" />
                                )}
                                {t("forceRunButton")}
                            </Button>
                        </div>
                    )}
                </CardContent>
            </Card>

            <Dialog open={confirmOpen} onOpenChange={setConfirmOpen}>
                <DialogContent>
                    <DialogHeader>
                        <DialogTitle>
                            {t("forceRunDialogTitle")}
                        </DialogTitle>
                        <DialogDescription>
                            {t("forceRunDialogBody")}
                        </DialogDescription>
                    </DialogHeader>
                    <DialogFooter>
                        <Button
                            variant="ghost"
                            size="sm"
                            onClick={() => setConfirmOpen(false)}
                        >
                            {t("cancelButton")}
                        </Button>
                        <Button
                            size="sm"
                            onClick={onRunNow}
                            data-testid="curator-force-run-confirm"
                        >
                            {t("forceRunConfirm")}
                        </Button>
                    </DialogFooter>
                </DialogContent>
            </Dialog>
        </div>
    );
}

function NumericField({
    label,
    description,
    field,
    value,
    onChange,
    min,
    max,
    source,
    showSlider = false,
    readOnly,
    onResetField,
    resetLabel,
    showReset,
    sourceWorkspaceLabel,
    sourcePlatformLabel,
}: {
    label: string;
    description: string;
    field: string;
    value: number;
    onChange: (v: number) => void;
    min: number;
    max: number;
    source: CuratorFieldSource;
    showSlider?: boolean;
    readOnly?: boolean;
    onResetField?: () => void;
    resetLabel: string;
    showReset?: boolean;
    sourceWorkspaceLabel: string;
    sourcePlatformLabel: string;
}) {
    const handle = (raw: string) => {
        const n = Number(raw);
        if (Number.isFinite(n)) onChange(Math.max(min, Math.min(max, n)));
    };

    return (
        <div className="space-y-2" data-testid={`curator-field-${field}`}>
            <div className="flex items-center justify-between gap-2">
                <Label className="text-sm font-medium" htmlFor={`f-${field}`}>
                    {label}
                </Label>
                <div className="flex items-center gap-2">
                    <SourceBadge
                        source={source}
                        workspaceLabel={sourceWorkspaceLabel}
                        platformLabel={sourcePlatformLabel}
                    />
                    {showReset && onResetField && (
                        <Button
                            type="button"
                            size="sm"
                            variant="ghost"
                            className="h-6 px-2 text-xs"
                            onClick={onResetField}
                            data-testid={`curator-reset-${field}`}
                        >
                            <IconRotate className="size-3" />
                            {resetLabel}
                        </Button>
                    )}
                </div>
            </div>
            <p className="text-xs sh-muted">{description}</p>
            <div className="flex items-center gap-3">
                {showSlider && (
                    <input
                        type="range"
                        min={min}
                        max={max}
                        value={value}
                        disabled={readOnly}
                        onChange={(e) => handle(e.target.value)}
                        className="h-1 w-full max-w-xs cursor-pointer appearance-none rounded-full bg-black/10 dark:bg-white/15 disabled:cursor-not-allowed disabled:opacity-50"
                        data-testid={`curator-slider-${field}`}
                    />
                )}
                <Input
                    id={`f-${field}`}
                    type="number"
                    min={min}
                    max={max}
                    value={value}
                    disabled={readOnly}
                    onChange={(e) => handle(e.target.value)}
                    className="w-28"
                    data-testid={`curator-input-${field}`}
                />
                <span className="text-xs sh-muted">
                    [{min}–{max}]
                </span>
            </div>
        </div>
    );
}

function SourceBadge({
    source,
    workspaceLabel,
    platformLabel,
}: {
    source: CuratorFieldSource;
    workspaceLabel?: string;
    platformLabel?: string;
}) {
    const isWorkspace = source === "workspace";
    return (
        <Badge
            variant="outline"
            className={cn(
                "text-[10px]",
                isWorkspace
                    ? "border-[rgb(var(--color-primary))] text-[rgb(var(--color-primary))]"
                    : "sh-muted",
            )}
        >
            {isWorkspace
                ? workspaceLabel ?? "workspace"
                : platformLabel ?? "platform default"}
        </Badge>
    );
}

function Stat({ label, value }: { label: string; value: string }) {
    return (
        <div>
            <p className="text-[11px] uppercase tracking-wider sh-muted">
                {label}
            </p>
            <p className="text-sm font-medium">{value}</p>
        </div>
    );
}
