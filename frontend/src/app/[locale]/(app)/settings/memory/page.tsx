"use client";

import { useEffect, useMemo, useState } from "react";
import {
  IconBrain,
  IconEdit,
  IconLoader2,
  IconPlus,
  IconSearch,
  IconSparkles,
  IconTrash,
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
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
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
import { Textarea } from "@/components/ui/textarea";
import { PageHeader } from "@/components/ui/page-header";
import {
  type MemoryKind,
  type MemoryRead,
  type MemoryScope,
  useCreateMemory,
  useDeleteMemory,
  useMemories,
  useMemoryStats,
  useRecallMemory,
  useUpdateMemory,
} from "@/hooks/use-memories";
import { relativeTime } from "@/lib/utils";

const SCOPES: (MemoryScope | "all")[] = ["all", "user", "assistant", "workspace"];
const KINDS: (MemoryKind | "all")[] = ["all", "kv", "episodic", "semantic"];

export default function MemorySettingsPage() {
  const t = useTranslations("settings.memory");
  const [scope, setScope] = useState<MemoryScope | "all">("all");
  const [kind, setKind] = useState<MemoryKind | "all">("all");
  const [q, setQ] = useState("");
  const [debouncedQ, setDebouncedQ] = useState("");

  useEffect(() => {
    const id = setTimeout(() => setDebouncedQ(q), 300);
    return () => clearTimeout(id);
  }, [q]);

  const { data: memories, isLoading } = useMemories({
    scope: scope === "all" ? null : scope,
    kind: kind === "all" ? null : kind,
    q: debouncedQ || null,
  });
  const { data: stats } = useMemoryStats();

  const rows = memories ?? [];

  return (
    <div>
      <PageHeader
        title={t("title")}
        description={t("description")}
        actions={<CreateMemoryDialog />}
      />

      <StatsRow stats={stats} />

      <Card className="mb-3">
        <CardContent className="grid gap-3 py-3 sm:grid-cols-[160px_160px_1fr]">
          <div className="grid gap-1.5">
            <Label className="text-[11px] sh-muted">{t("filter.scope")}</Label>
            <Select
              value={scope}
              onValueChange={(v) => setScope(v as MemoryScope | "all")}
            >
              <SelectTrigger>
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {SCOPES.map((s) => (
                  <SelectItem key={s} value={s}>
                    {t(`scope.${s}`)}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
          <div className="grid gap-1.5">
            <Label className="text-[11px] sh-muted">{t("filter.kind")}</Label>
            <Select
              value={kind}
              onValueChange={(v) => setKind(v as MemoryKind | "all")}
            >
              <SelectTrigger>
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {KINDS.map((k) => (
                  <SelectItem key={k} value={k}>
                    {t(`kind.${k}`)}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
          <div className="grid gap-1.5">
            <Label className="text-[11px] sh-muted">{t("filter.search")}</Label>
            <div className="relative">
              <IconSearch className="pointer-events-none absolute left-2 top-1/2 size-3.5 -translate-y-1/2 sh-muted" />
              <Input
                value={q}
                onChange={(e) => setQ(e.target.value)}
                placeholder={t("filter.searchPlaceholder")}
                className="pl-7"
              />
            </div>
          </div>
        </CardContent>
      </Card>

      <RecallPanel />

      {isLoading && <Skeleton className="h-40" />}

      {!isLoading && rows.length === 0 && (
        <Card>
          <CardContent className="py-10 text-center">
            <IconBrain className="mx-auto size-8 sh-muted" />
            <p className="mt-3 text-sm sh-muted">
              {debouncedQ ? t("emptyForQuery") : t("empty")}
            </p>
          </CardContent>
        </Card>
      )}

      <div className="space-y-2">
        {rows.map((m) => (
          <MemoryRow key={m.id} mem={m} />
        ))}
      </div>
    </div>
  );
}

function StatsRow({
  stats,
}: {
  stats: { total: number; by_scope: Record<string, number>; by_kind: Record<string, number> } | undefined;
}) {
  const t = useTranslations("settings.memory");
  return (
    <div className="mb-3 grid grid-cols-2 gap-3 sm:grid-cols-4">
      <StatCard label={t("stats.total")} value={stats?.total ?? 0} />
      <StatCard
        label={t("scope.user")}
        value={stats?.by_scope["user"] ?? 0}
      />
      <StatCard
        label={t("scope.assistant")}
        value={stats?.by_scope["assistant"] ?? 0}
      />
      <StatCard
        label={t("scope.workspace")}
        value={stats?.by_scope["workspace"] ?? 0}
      />
    </div>
  );
}

function StatCard({ label, value }: { label: string; value: number }) {
  return (
    <Card>
      <CardContent className="py-3">
        <div className="text-[11px] uppercase sh-muted">{label}</div>
        <div className="text-2xl font-semibold tabular-nums">
          {value.toLocaleString()}
        </div>
      </CardContent>
    </Card>
  );
}

function RecallPanel() {
  const t = useTranslations("settings.memory");
  const recall = useRecallMemory();
  const [q, setQ] = useState("");
  const [minScore, setMinScore] = useState(0.3);

  const run = async () => {
    if (!q.trim()) return;
    try {
      await recall.mutateAsync({
        query: q.trim(),
        limit: 6,
        min_score: minScore,
      });
    } catch {
      toast.error(t("recall.failed"));
    }
  };

  const hits = recall.data ?? [];

  return (
    <Card className="mb-3">
      <CardHeader className="pb-2">
        <CardTitle className="flex items-center gap-2 text-base">
          <IconSparkles className="size-4" />
          {t("recall.title")}
        </CardTitle>
        <CardDescription>{t("recall.description")}</CardDescription>
      </CardHeader>
      <CardContent className="space-y-2">
        <div className="flex items-center gap-2">
          <Input
            value={q}
            onChange={(e) => setQ(e.target.value)}
            placeholder={t("recall.placeholder")}
            onKeyDown={(e) => {
              if (e.key === "Enter") void run();
            }}
            className="flex-1"
          />
          <Input
            type="number"
            min={0}
            max={1}
            step={0.05}
            value={minScore}
            onChange={(e) => setMinScore(Number(e.target.value) || 0.3)}
            className="w-[96px]"
            title={t("recall.minScoreHint")}
          />
          <Button
            onClick={run}
            disabled={recall.isPending || !q.trim()}
          >
            {recall.isPending ? (
              <IconLoader2 className="size-4 animate-spin" />
            ) : (
              <IconSearch className="size-4" />
            )}
            {t("recall.go")}
          </Button>
        </div>
        {recall.isSuccess && hits.length === 0 && (
          <p className="py-3 text-center text-xs sh-muted">
            {t("recall.empty")}
          </p>
        )}
        {hits.length > 0 && (
          <ol className="flex flex-col gap-1.5">
            {hits.map((h, i) => (
              <li
                key={h.memory.id}
                className="rounded border p-2 text-[13px]"
              >
                <div className="mb-1 flex items-center gap-2 text-[11px] sh-muted">
                  <span className="font-mono">#{i + 1}</span>
                  <Badge variant="outline">{h.memory.scope}</Badge>
                  <Badge variant="default">{h.memory.kind}</Badge>
                  <span className="ml-auto rounded bg-green-500/10 px-1.5 py-0.5 font-mono tabular-nums text-green-700 dark:text-green-400">
                    {h.score.toFixed(3)}
                  </span>
                </div>
                <p className="line-clamp-3 whitespace-pre-wrap break-words">
                  {h.memory.content}
                </p>
              </li>
            ))}
          </ol>
        )}
      </CardContent>
    </Card>
  );
}

function MemoryRow({ mem }: { mem: MemoryRead }) {
  const t = useTranslations("settings.memory");
  const locale = useLocale();
  const remove = useDeleteMemory();
  const [editing, setEditing] = useState(false);

  const onDelete = async () => {
    if (!confirm(t("confirmDelete"))) return;
    try {
      await remove.mutateAsync(mem.id);
      toast.success(t("deleted"));
    } catch {
      toast.error(t("deleteFailed"));
    }
  };

  return (
    <Card>
      <CardHeader className="pb-2">
        <div className="flex items-start gap-2">
          <Badge variant="outline">{mem.scope}</Badge>
          <Badge variant={mem.kind === "kv" ? "primary" : "default"}>
            {mem.kind}
          </Badge>
          {mem.key && <Badge variant="outline">{mem.key}</Badge>}
          <span className="ml-auto text-[10px] sh-muted">
            {relativeTime(mem.updated_at, locale)}
          </span>
        </div>
        <CardTitle className="mt-1 break-words whitespace-pre-wrap text-sm font-normal">
          {mem.content}
        </CardTitle>
        <CardDescription className="flex items-center gap-2 text-[11px]">
          <span>
            {t("confidenceLabel", { n: mem.confidence.toFixed(2) })}
          </span>
          {mem.embedding_model && (
            <span className="font-mono text-[10px]">· {mem.embedding_model}</span>
          )}
          {mem.ttl_at && (
            <span>· {t("expiresAt", { when: new Date(mem.ttl_at).toLocaleString(locale) })}</span>
          )}
        </CardDescription>
      </CardHeader>
      <CardContent className="flex justify-end gap-1 pt-0">
        <Button
          variant="ghost"
          size="sm"
          onClick={() => setEditing(true)}
        >
          <IconEdit className="size-3.5" />
          {t("edit")}
        </Button>
        <Button
          variant="ghost"
          size="sm"
          onClick={onDelete}
          disabled={remove.isPending}
        >
          <IconTrash className="size-3.5" />
          {t("delete")}
        </Button>
      </CardContent>

      <EditDialog mem={mem} open={editing} onOpenChange={setEditing} />
    </Card>
  );
}

function EditDialog({
  mem,
  open,
  onOpenChange,
}: {
  mem: MemoryRead;
  open: boolean;
  onOpenChange: (v: boolean) => void;
}) {
  const t = useTranslations("settings.memory");
  const update = useUpdateMemory(mem.id);
  const [content, setContent] = useState(mem.content);
  const [confidence, setConfidence] = useState(mem.confidence);

  const dirty = content !== mem.content || confidence !== mem.confidence;

  const submit = async () => {
    try {
      await update.mutateAsync({
        content: content !== mem.content ? content : undefined,
        confidence:
          confidence !== mem.confidence ? confidence : undefined,
      });
      toast.success(t("saved"));
      onOpenChange(false);
    } catch {
      toast.error(t("saveFailed"));
    }
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-[520px]">
        <DialogHeader>
          <DialogTitle>{t("editTitle")}</DialogTitle>
        </DialogHeader>
        <div className="space-y-3">
          <div className="grid gap-1.5">
            <Label>{t("form.content")}</Label>
            <Textarea
              value={content}
              onChange={(e) => setContent(e.target.value)}
              className="min-h-[160px]"
            />
            <p className="text-[11px] sh-muted">{t("editContentHint")}</p>
          </div>
          <div className="grid gap-1.5">
            <Label>{t("form.confidence")}</Label>
            <Input
              type="number"
              min={0}
              max={1}
              step={0.05}
              value={confidence}
              onChange={(e) =>
                setConfidence(Math.max(0, Math.min(1, Number(e.target.value) || 0)))
              }
            />
          </div>
        </div>
        <DialogFooter>
          <Button variant="ghost" onClick={() => onOpenChange(false)}>
            {t("cancel")}
          </Button>
          <Button
            onClick={submit}
            disabled={update.isPending || !dirty || !content.trim()}
          >
            {update.isPending && (
              <IconLoader2 className="size-4 animate-spin" />
            )}
            {t("save")}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

function CreateMemoryDialog() {
  const t = useTranslations("settings.memory");
  const create = useCreateMemory();
  const [open, setOpen] = useState(false);
  const [scope, setScope] = useState<MemoryScope>("user");
  const [kind, setKind] = useState<MemoryKind>("semantic");
  const [key, setKey] = useState("");
  const [content, setContent] = useState("");

  const submit = async () => {
    try {
      await create.mutateAsync({
        scope,
        kind,
        key: kind === "kv" ? key || null : null,
        content,
      });
      toast.success(t("saved"));
      setOpen(false);
      setContent("");
      setKey("");
    } catch {
      toast.error(t("saveFailed"));
    }
  };

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger asChild>
        <Button size="sm">
          <IconPlus className="size-4" />
          {t("new")}
        </Button>
      </DialogTrigger>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>{t("newTitle")}</DialogTitle>
        </DialogHeader>
        <div className="space-y-3">
          <div className="grid grid-cols-2 gap-3">
            <div className="grid gap-1.5">
              <Label>{t("form.scope")}</Label>
              <Select
                value={scope}
                onValueChange={(v) => setScope(v as MemoryScope)}
              >
                <SelectTrigger>
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="user">{t("scope.user")}</SelectItem>
                  <SelectItem value="assistant">
                    {t("scope.assistant")}
                  </SelectItem>
                  <SelectItem value="workspace">
                    {t("scope.workspace")}
                  </SelectItem>
                </SelectContent>
              </Select>
            </div>
            <div className="grid gap-1.5">
              <Label>{t("form.kind")}</Label>
              <Select
                value={kind}
                onValueChange={(v) => setKind(v as MemoryKind)}
              >
                <SelectTrigger>
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="kv">{t("kind.kv")}</SelectItem>
                  <SelectItem value="episodic">{t("kind.episodic")}</SelectItem>
                  <SelectItem value="semantic">{t("kind.semantic")}</SelectItem>
                </SelectContent>
              </Select>
            </div>
          </div>
          {kind === "kv" && (
            <div className="grid gap-1.5">
              <Label>{t("form.key")}</Label>
              <Input
                value={key}
                onChange={(e) => setKey(e.target.value)}
                placeholder="preferred_editor"
              />
            </div>
          )}
          <div className="grid gap-1.5">
            <Label>{t("form.content")}</Label>
            <Textarea
              value={content}
              onChange={(e) => setContent(e.target.value)}
              className="min-h-[100px]"
              placeholder={t("form.contentPlaceholder")}
            />
          </div>
        </div>
        <DialogFooter>
          <Button variant="ghost" onClick={() => setOpen(false)}>
            {t("cancel")}
          </Button>
          <Button
            onClick={submit}
            disabled={create.isPending || !content.trim()}
          >
            {create.isPending && (
              <IconLoader2 className="size-4 animate-spin" />
            )}
            {t("save")}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
