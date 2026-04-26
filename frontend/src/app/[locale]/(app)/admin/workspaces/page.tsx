"use client";

import { useEffect, useState } from "react";
import {
  IconBuildingCommunity,
  IconLoader2,
  IconRobot,
  IconSearch,
  IconTrash,
  IconUsers,
} from "@tabler/icons-react";
import { useLocale, useTranslations } from "next-intl";
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
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";
import { PageHeader } from "@/components/ui/page-header";
import {
  type WorkspaceAdminRow,
  type WorkspacePlan,
  useAdminWorkspace,
  useAdminWorkspaces,
  useDeleteWorkspace,
  usePatchWorkspace,
} from "@/hooks/use-admin";
import { relativeTime } from "@/lib/utils";

const PLANS: WorkspacePlan[] = ["free", "team", "business", "enterprise"];

export default function AdminWorkspacesPage() {
  const t = useTranslations("admin.workspaces");
  const [q, setQ] = useState("");
  const [debouncedQ, setDebouncedQ] = useState("");
  const [planFilter, setPlanFilter] = useState<WorkspacePlan | "">("");
  const [focusId, setFocusId] = useState<string | null>(null);

  useEffect(() => {
    const id = setTimeout(() => setDebouncedQ(q), 300);
    return () => clearTimeout(id);
  }, [q]);

  const { data, isLoading } = useAdminWorkspaces({
    q: debouncedQ || undefined,
    plan: planFilter || undefined,
  });

  return (
    <div>
      <PageHeader
        title={t("title")}
        description={t("description")}
        actions={
          <span className="text-[11px] sh-muted">
            {t("showing", { n: (data ?? []).length })}
          </span>
        }
      />

      <Card className="mb-3">
        <CardContent className="grid gap-3 py-3 sm:grid-cols-[1fr_160px]">
          <div className="relative">
            <IconSearch className="pointer-events-none absolute left-2 top-1/2 size-3.5 -translate-y-1/2 sh-muted" />
            <Input
              value={q}
              onChange={(e) => setQ(e.target.value)}
              placeholder={t("searchPlaceholder")}
              className="pl-7"
            />
          </div>
          <Select
            value={planFilter || "all"}
            onValueChange={(v) =>
              setPlanFilter(v === "all" ? "" : (v as WorkspacePlan))
            }
          >
            <SelectTrigger>
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="all">{t("filter.anyPlan")}</SelectItem>
              {PLANS.map((p) => (
                <SelectItem key={p} value={p}>
                  {p}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </CardContent>
      </Card>

      {isLoading && <Skeleton className="h-60" />}

      {!isLoading && (data ?? []).length === 0 && (
        <Card>
          <CardContent className="py-10 text-center text-sm sh-muted">
            <IconBuildingCommunity className="mx-auto size-8" />
            <p className="mt-2">{t("empty")}</p>
          </CardContent>
        </Card>
      )}

      <div className="grid grid-cols-1 gap-2 sm:grid-cols-2 lg:grid-cols-3">
        {(data ?? []).map((row) => (
          <WorkspaceCard
            key={row.id}
            row={row}
            onOpen={() => setFocusId(row.id)}
          />
        ))}
      </div>

      <WorkspaceDialog
        workspaceId={focusId}
        open={Boolean(focusId)}
        onOpenChange={(v) => !v && setFocusId(null)}
      />
    </div>
  );
}

function WorkspaceCard({
  row,
  onOpen,
}: {
  row: WorkspaceAdminRow;
  onOpen: () => void;
}) {
  const t = useTranslations("admin.workspaces");
  const locale = useLocale();
  return (
    <Card
      className="cursor-pointer transition-colors hover:bg-black/3 dark:hover:bg-white/3"
      onClick={onOpen}
    >
      <CardHeader className="pb-2">
        <div className="flex items-center gap-2">
          <IconBuildingCommunity className="size-4 text-blue-500" />
          <CardTitle className="flex-1 truncate text-base">{row.name}</CardTitle>
          <Badge variant="outline">{row.plan}</Badge>
        </div>
        <CardDescription className="flex items-center gap-1 text-[11px]">
          <span className="font-mono">{row.slug}</span>
          <span>·</span>
          <span>{relativeTime(row.created_at, locale)}</span>
        </CardDescription>
      </CardHeader>
      <CardContent className="flex items-center gap-3 pt-0 text-xs sh-muted">
        <span className="inline-flex items-center gap-1">
          <IconUsers className="size-3" />
          {t("members", { n: row.member_count })}
        </span>
        <span className="inline-flex items-center gap-1">
          <IconRobot className="size-3" />
          {t("agents", { n: row.agent_count })}
        </span>
        <span className="ml-auto font-mono tabular-nums">
          {row.session_count} sess
        </span>
      </CardContent>
    </Card>
  );
}

function WorkspaceDialog({
  workspaceId,
  open,
  onOpenChange,
}: {
  workspaceId: string | null;
  open: boolean;
  onOpenChange: (v: boolean) => void;
}) {
  const t = useTranslations("admin.workspaces");
  const { data: detail, isLoading } = useAdminWorkspace(
    open ? workspaceId : null,
  );
  const patch = usePatchWorkspace();
  const del = useDeleteWorkspace();

  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [plan, setPlan] = useState<WorkspacePlan>("free");

  useEffect(() => {
    if (!detail) return;
    setName(detail.name);
    setDescription(detail.description ?? "");
    setPlan(detail.plan);
  }, [detail?.id]);

  const dirty =
    detail != null &&
    (name !== detail.name ||
      description !== (detail.description ?? "") ||
      plan !== detail.plan);

  const save = async () => {
    if (!detail) return;
    try {
      await patch.mutateAsync({
        id: detail.id,
        name,
        description: description || null,
        plan,
      });
      toast.success(t("saved"));
    } catch {
      toast.error(t("saveFailed"));
    }
  };

  const destroy = async () => {
    if (!detail) return;
    if (!confirm(t("confirmDelete", { name: detail.name }))) return;
    try {
      await del.mutateAsync(detail.id);
      toast.success(t("deleted"));
      onOpenChange(false);
    } catch {
      toast.error(t("deleteFailed"));
    }
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-[520px]">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            <IconBuildingCommunity className="size-4" />
            {detail?.name ?? t("loading")}
          </DialogTitle>
          <DialogDescription>
            <span className="font-mono">{detail?.slug}</span>
          </DialogDescription>
        </DialogHeader>

        {isLoading || !detail ? (
          <Skeleton className="h-40" />
        ) : (
          <div className="space-y-3">
            <div className="grid grid-cols-3 gap-2 text-xs">
              <Stat label={t("members", { n: "" })} value={detail.member_count} />
              <Stat label={t("agents", { n: "" })} value={detail.agent_count} />
              <Stat label="sessions" value={detail.session_count} />
            </div>
            <div className="grid gap-1.5">
              <Label>{t("nameLabel")}</Label>
              <Input value={name} onChange={(e) => setName(e.target.value)} />
            </div>
            <div className="grid gap-1.5">
              <Label>{t("descriptionLabel")}</Label>
              <Input
                value={description}
                onChange={(e) => setDescription(e.target.value)}
              />
            </div>
            <div className="grid gap-1.5">
              <Label>{t("planLabel")}</Label>
              <Select
                value={plan}
                onValueChange={(v) => setPlan(v as WorkspacePlan)}
              >
                <SelectTrigger>
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {PLANS.map((p) => (
                    <SelectItem key={p} value={p}>
                      {p}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
          </div>
        )}

        <DialogFooter>
          {detail && (
            <div className="flex w-full flex-wrap items-center gap-2">
              <Button
                variant="destructive"
                size="sm"
                onClick={destroy}
                disabled={del.isPending}
              >
                <IconTrash className="size-4" />
                {t("delete")}
              </Button>
              <Button
                className="ml-auto"
                variant="ghost"
                onClick={() => onOpenChange(false)}
              >
                {t("close")}
              </Button>
              <Button
                onClick={save}
                disabled={!dirty || patch.isPending}
              >
                {patch.isPending && (
                  <IconLoader2 className="size-4 animate-spin" />
                )}
                {t("save")}
              </Button>
            </div>
          )}
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

function Stat({ label, value }: { label: string; value: number }) {
  return (
    <div className="rounded border p-2 text-center">
      <div className="text-[10px] sh-muted">{label.trim() || " "}</div>
      <div className="font-semibold tabular-nums">{value}</div>
    </div>
  );
}
