"use client";

import { useState } from "react";
import {
  IconArchive,
  IconPin,
  IconPinnedOff,
  IconPlayerPlay,
  IconPlayerStop,
  IconTrash,
} from "@tabler/icons-react";
import { useTranslations } from "next-intl";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { SkillDetailDrawer } from "@/components/skills/SkillDetailDrawer";
import { SkillStateBadge } from "@/components/skills/SkillStateBadge";
import {
  type SkillPackPersisted,
  useArchiveSkill,
  useDeprecateSkill,
  usePinSkill,
  useRestoreSkill,
  useSkillPacks,
  useUnpinSkill,
} from "@/hooks/use-skill-lifecycle";
import { ApiError } from "@/lib/api";

export function SkillPacksSection() {
  const t = useTranslations("skillLifecycle");
  const ts = useTranslations("settings.skills");
  const { data, isLoading } = useSkillPacks();

  if (isLoading) return <Skeleton className="h-24" />;
  if (!data || data.length === 0) return null;

  return (
    <section className="mb-4">
      <h2 className="mb-2 text-[11px] font-medium uppercase sh-muted">
        {ts("workspaceSection")} ({data.length})
      </h2>
      <div className="grid gap-3 sm:grid-cols-2">
        {data.map((pack) => (
          <PackCard key={pack.id} pack={pack} t={t} />
        ))}
      </div>
    </section>
  );
}

interface CardProps {
  pack: SkillPackPersisted;
  t: ReturnType<typeof useTranslations<"skillLifecycle">>;
}

function PackCard({ pack, t }: CardProps) {
  const [open, setOpen] = useState(false);
  const pin = usePinSkill();
  const unpin = useUnpinSkill();
  const archive = useArchiveSkill();
  const restore = useRestoreSkill();
  const deprecate = useDeprecateSkill();

  const reportError = (e: unknown, fallback: string) => {
    if (e instanceof ApiError) {
      const known: Record<string, string> = {
        "skill.invalid_transition": t("feedback.invalidTransition"),
        "skill.terminal_state": t("feedback.terminal"),
      };
      toast.error(known[e.code] ?? fallback);
      return;
    }
    toast.error(fallback);
  };

  const handlePin = async () => {
    if (!confirm(t("confirm.pin", { name: pack.name }))) return;
    try {
      await pin.mutateAsync({ packId: pack.id, reason: "user pinned" });
      toast.success(t("feedback.pinned"));
    } catch (e) {
      reportError(e, t("feedback.actionFailed"));
    }
  };
  const handleUnpin = async () => {
    if (!confirm(t("confirm.unpin", { name: pack.name }))) return;
    try {
      await unpin.mutateAsync({ packId: pack.id, reason: "user unpinned" });
      toast.success(t("feedback.unpinned"));
    } catch (e) {
      reportError(e, t("feedback.actionFailed"));
    }
  };
  const handleArchive = async () => {
    if (!confirm(t("confirm.archive", { name: pack.name }))) return;
    try {
      await archive.mutateAsync({ packId: pack.id, reason: "user archived" });
      toast.success(t("feedback.archived"));
    } catch (e) {
      reportError(e, t("feedback.actionFailed"));
    }
  };
  const handleRestore = async () => {
    if (!confirm(t("confirm.restore", { name: pack.name }))) return;
    try {
      await restore.mutateAsync({ packId: pack.id, reason: "user restored" });
      toast.success(t("feedback.restored"));
    } catch (e) {
      reportError(e, t("feedback.actionFailed"));
    }
  };
  const handleDeprecate = async () => {
    if (!confirm(t("confirm.deprecate", { name: pack.name }))) return;
    try {
      await deprecate.mutateAsync({ packId: pack.id, reason: "user deprecated" });
      toast.success(t("feedback.deprecated"));
    } catch (e) {
      reportError(e, t("feedback.actionFailed"));
    }
  };

  const showPin = !pack.pinned && pack.state !== "tombstone";
  const showUnpin = pack.pinned;
  const showArchive =
    pack.state !== "archived" && pack.state !== "tombstone";
  const showRestore = pack.state === "archived";
  const showDeprecate = pack.state === "active";

  return (
    <Card className="flex flex-col">
      <CardHeader className="flex-1 pb-2">
        <div className="flex items-center gap-2">
          <CardTitle className="flex-1 truncate text-base">{pack.name}</CardTitle>
          <SkillStateBadge state={pack.state} pinned={pack.pinned} />
        </div>
        {pack.description && (
          <CardDescription className="line-clamp-2">
            {pack.description}
          </CardDescription>
        )}
      </CardHeader>
      <CardContent className="space-y-2 pt-0">
        <div className="flex flex-wrap items-center gap-1">
          <Button
            variant="outline"
            size="sm"
            className="flex-1 min-w-[5rem]"
            onClick={() => setOpen(true)}
          >
            {t("drawer.title")}
          </Button>
          {showPin && (
            <Button
              variant="outline"
              size="sm"
              onClick={handlePin}
              disabled={pin.isPending}
              title={t("actions.pin")}
            >
              <IconPin className="size-3.5" />
            </Button>
          )}
          {showUnpin && (
            <Button
              variant="outline"
              size="sm"
              onClick={handleUnpin}
              disabled={unpin.isPending}
              title={t("actions.unpin")}
            >
              <IconPinnedOff className="size-3.5" />
            </Button>
          )}
          {showArchive && (
            <Button
              variant="outline"
              size="sm"
              onClick={handleArchive}
              disabled={archive.isPending}
              title={t("actions.archive")}
            >
              <IconArchive className="size-3.5" />
            </Button>
          )}
          {showRestore && (
            <Button
              variant="outline"
              size="sm"
              onClick={handleRestore}
              disabled={restore.isPending}
              title={t("actions.restore")}
            >
              <IconPlayerPlay className="size-3.5" />
            </Button>
          )}
          {showDeprecate && (
            <Button
              variant="outline"
              size="sm"
              onClick={handleDeprecate}
              disabled={deprecate.isPending}
              title={t("actions.deprecate")}
            >
              <IconPlayerStop className="size-3.5" />
            </Button>
          )}
          {pack.state === "tombstone" && (
            <span className="ml-auto text-[10px] sh-muted">
              <IconTrash className="inline size-3.5" />
            </span>
          )}
        </div>
        <p className="text-[10px] sh-muted">v{pack.version}</p>
      </CardContent>

      <SkillDetailDrawer pack={pack} open={open} onOpenChange={setOpen} />
    </Card>
  );
}
