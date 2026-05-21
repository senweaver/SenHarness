"use client";

import { useState } from "react";
import {
  IconBuildingWarehouse,
  IconCopy,
  IconFileCode,
  IconPackage,
  IconPlus,
  IconPuzzle,
  IconTrash,
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
import { PageHeader } from "@/components/ui/page-header";
import { Skeleton } from "@/components/ui/skeleton";
import { SkillImportHubDialog } from "@/components/skills/SkillImportHubDialog";
import { SkillPacksSection } from "@/components/skills/SkillPacksSection";
import {
  type SkillRead,
  useDeleteSkill,
  useSkillDetail,
  useSkills,
} from "@/hooks/use-skills";


export default function SkillsSettingsPage() {
  const t = useTranslations("settings.skills");
  const tImport = useTranslations("skillImport");
  const { data, isLoading } = useSkills();
  const [importOpen, setImportOpen] = useState(false);
  const bundled = (data ?? []).filter((s) => s.source === "bundled");
  const workspace = (data ?? []).filter((s) => s.source === "workspace");

  return (
    <div className="p-6">
      <PageHeader
        title={t("title")}
        description={t("description")}
        actions={
          <Button size="sm" onClick={() => setImportOpen(true)}>
            <IconPlus className="size-4" />
            {tImport("title")}
          </Button>
        }
      />
      <SkillImportHubDialog open={importOpen} onOpenChange={setImportOpen} />

      <SkillPacksSection />

      {isLoading && <Skeleton className="h-40" />}

      {!isLoading && (data ?? []).length === 0 && (
        <Card>
          <CardContent className="py-10 text-center">
            <IconPuzzle className="mx-auto size-8 sh-muted" />
            <p className="mt-3 text-sm sh-muted">{t("empty")}</p>
          </CardContent>
        </Card>
      )}

      {workspace.length > 0 && (
        <section className="mb-4">
          <h2 className="mb-2 text-[11px] font-medium uppercase sh-muted">
            {t("workspaceSection")} ({workspace.length})
          </h2>
          <div className="grid gap-3 sm:grid-cols-2">
            {workspace.map((s) => (
              <SkillCard key={`w-${s.slug}`} skill={s} />
            ))}
          </div>
        </section>
      )}

      {bundled.length > 0 && (
        <section>
          <h2 className="mb-2 text-[11px] font-medium uppercase sh-muted">
            {t("bundledSection")} ({bundled.length})
          </h2>
          <div className="grid gap-3 sm:grid-cols-2">
            {bundled.map((s) => (
              <SkillCard key={`b-${s.slug}`} skill={s} />
            ))}
          </div>
        </section>
      )}

      <p className="mt-6 text-[11px] sh-muted">{t("footnote")}</p>
    </div>
  );
}

function SkillCard({ skill }: { skill: SkillRead }) {
  const t = useTranslations("settings.skills");
  const [detailOpen, setDetailOpen] = useState(false);
  const remove = useDeleteSkill();

  const copySnippet = async () => {
    try {
      await navigator.clipboard.writeText(
        `"skills": ["${skill.name}"]`,
      );
      toast.success(t("snippetCopied"));
    } catch {
      toast.error(t("snippetFailed"));
    }
  };

  const onDelete = async () => {
    if (!confirm(t("confirmDelete", { name: skill.name }))) return;
    try {
      await remove.mutateAsync(skill.slug);
      toast.success(t("deleted"));
    } catch {
      toast.error(t("deleteFailed"));
    }
  };

  return (
    <Card className="flex flex-col">
      <CardHeader className="flex-1 pb-2">
        <div className="flex items-center gap-2">
          {skill.source === "bundled" ? (
            <IconPackage className="size-4 sh-muted" />
          ) : (
            <IconBuildingWarehouse className="size-4 sh-muted" />
          )}
          <CardTitle className="flex-1 truncate text-base">
            {skill.name}
          </CardTitle>
          <Badge variant={skill.source === "bundled" ? "outline" : "primary"}>
            {skill.source}
          </Badge>
        </div>
        {skill.description && (
          <CardDescription className="line-clamp-2">
            {skill.description}
          </CardDescription>
        )}
      </CardHeader>
      <CardContent className="space-y-2 pt-0">
        {skill.prompt_preview && (
          <pre className="line-clamp-3 whitespace-pre-wrap rounded bg-black/5 p-2 text-[11px] dark:bg-white/5">
            {skill.prompt_preview}
          </pre>
        )}
        <div className="flex items-center gap-1">
          <Button
            variant="outline"
            size="sm"
            className="flex-1"
            onClick={() => setDetailOpen(true)}
          >
            <IconFileCode className="size-3.5" />
            {t("viewSource")}
          </Button>
          <Button
            variant="outline"
            size="sm"
            onClick={copySnippet}
            title={t("copyUsageSnippet")}
          >
            <IconCopy className="size-3.5" />
          </Button>
          {skill.source === "workspace" && (
            <Button
              variant="outline"
              size="icon"
              className="size-8"
              onClick={onDelete}
              disabled={remove.isPending}
              title={t("delete")}
            >
              <IconTrash className="size-3.5 text-red-600" />
            </Button>
          )}
        </div>
        <p className="text-[10px] sh-muted">
          {t("bodyLength", { n: skill.body_length })}
        </p>
      </CardContent>

      <DetailDialog
        skill={skill}
        open={detailOpen}
        onOpenChange={setDetailOpen}
      />
    </Card>
  );
}

function DetailDialog({
  skill,
  open,
  onOpenChange,
}: {
  skill: SkillRead;
  open: boolean;
  onOpenChange: (v: boolean) => void;
}) {
  const t = useTranslations("settings.skills");
  const { data: detail, isLoading } = useSkillDetail(
    open ? skill.source : undefined,
    open ? skill.slug : undefined,
  );

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-[720px]">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            <IconFileCode className="size-4" />
            {skill.name}
          </DialogTitle>
          <DialogDescription>{skill.description}</DialogDescription>
        </DialogHeader>

        {isLoading ? (
          <Skeleton className="h-60" />
        ) : detail ? (
          <pre className="max-h-[60vh] overflow-y-auto whitespace-pre-wrap break-words rounded-md bg-black/5 p-3 font-mono text-[12px] dark:bg-white/5">
            {detail.content}
          </pre>
        ) : (
          <p className="py-6 text-center text-sm sh-muted">
            {t("detailFailed")}
          </p>
        )}

        <DialogFooter>
          <Button onClick={() => onOpenChange(false)}>OK</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

