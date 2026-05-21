"use client";

import { useMemo, useState } from "react";
import {
  IconEdit,
  IconEye,
  IconEyeOff,
  IconKey,
  IconLoader2,
  IconPlus,
  IconRefresh,
  IconSearch,
  IconShieldCheck,
  IconTrash,
} from "@tabler/icons-react";
import { useLocale, useTranslations } from "next-intl";
import { toast } from "sonner";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { PageHeader } from "@/components/ui/page-header";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Switch } from "@/components/ui/switch";
import { Textarea } from "@/components/ui/textarea";
import {
  revealSecret,
  useCreateSecret,
  useDeleteSecret,
  useSecrets,
  useUpdateSecret,
  type SecretRead,
} from "@/hooks/use-secrets";
import { relativeTime } from "@/lib/utils";

const KIND_OPTIONS = [
  "generic",
  "api_key",
  "password",
  "oauth",
  "cookie_bag",
  "cert",
] as const;

export default function SecretsSettingsPage() {
  const t = useTranslations("settings.secrets");
  const tCommon = useTranslations("common");
  const locale = useLocale();
  const { data = [], isLoading, isFetching, refetch } = useSecrets();
  const [q, setQ] = useState("");

  const filtered = useMemo(() => {
    const needle = q.trim().toLowerCase();
    if (!needle) return data;
    return data.filter(
      (s) =>
        s.name.toLowerCase().includes(needle) ||
        s.kind.toLowerCase().includes(needle) ||
        String((s.metadata_json as { description?: string })?.description ?? "")
          .toLowerCase()
          .includes(needle),
    );
  }, [data, q]);

  return (
    <div>
      <PageHeader
        title={t("title")}
        description={t("description")}
        actions={
          <div className="flex items-center gap-2">
            <Button
              variant="outline"
              size="sm"
              onClick={() => refetch()}
              disabled={isFetching}
            >
              <IconRefresh
                className={isFetching ? "size-4 animate-spin" : "size-4"}
              />
            </Button>
            <SecretDialog mode="create" />
          </div>
        }
      />

      <Card className="mb-3">
        <CardContent className="py-3">
          <div className="relative">
            <IconSearch className="pointer-events-none absolute left-2 top-1/2 size-3.5 -translate-y-1/2 sh-muted" />
            <Input
              value={q}
              onChange={(e) => setQ(e.target.value)}
              placeholder={t("searchPlaceholder")}
              className="pl-7"
            />
          </div>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2 text-sm">
            <IconKey className="size-4 text-amber-500" />
            {t("countLabel", { n: filtered.length })}
            {isLoading && <IconLoader2 className="size-3 animate-spin" />}
          </CardTitle>
        </CardHeader>
        <CardContent className="divide-y">
          {!isLoading && filtered.length === 0 && (
            <p className="py-8 text-center text-xs sh-muted">
              {q.trim() ? t("emptySearch") : t("empty")}
            </p>
          )}
          {filtered.map((row) => (
            <SecretRow key={row.id} row={row} locale={locale} />
          ))}
        </CardContent>
      </Card>

      <p className="mt-4 text-[11px] sh-muted">{tCommon("note")}: {t("howToUseHint")}</p>
    </div>
  );
}

function SecretRow({ row, locale }: { row: SecretRead; locale: string }) {
  const t = useTranslations("settings.secrets");
  const del = useDeleteSecret();
  const [revealing, setRevealing] = useState(false);
  const [revealed, setRevealed] = useState<string | null>(null);
  const description = (row.metadata_json as { description?: string })?.description;

  const onReveal = async () => {
    if (revealed !== null) {
      setRevealed(null);
      return;
    }
    setRevealing(true);
    try {
      const v = await revealSecret(row.id);
      setRevealed(v);
      // Auto-hide after 30s so it doesn't stay on a shared screen.
      setTimeout(() => setRevealed(null), 30_000);
    } catch {
      toast.error(t("revealFailed"));
    } finally {
      setRevealing(false);
    }
  };

  const onDelete = async () => {
    if (!confirm(t("confirmDelete", { name: row.name }))) return;
    try {
      await del.mutateAsync(row.id);
      toast.success(t("deleted"));
    } catch {
      toast.error(t("deleteFailed"));
    }
  };

  return (
    <div className="flex items-start gap-3 py-2">
      <div className="min-w-0 flex-1">
        <div className="flex flex-wrap items-center gap-2">
          <span className="truncate font-mono text-sm font-medium">
            {row.name}
          </span>
          <Badge variant="outline">{row.kind}</Badge>
          {row.required_approval && (
            <Badge variant="warning" className="gap-1">
              <IconShieldCheck className="size-3" />
              {t("requiresApproval")}
            </Badge>
          )}
          <span className="ml-auto text-[10px] sh-muted">
            {relativeTime(row.updated_at, locale)}
          </span>
        </div>
        {description && (
          <p className="mt-0.5 text-[11px] sh-muted line-clamp-2">{description}</p>
        )}
        <div className="mt-1 font-mono text-[11px] sh-muted">
          {revealed !== null ? (
            <span className="break-all text-foreground">{revealed}</span>
          ) : (
            <span>{"•".repeat(12)}</span>
          )}
        </div>
      </div>
      <Button
        variant="ghost"
        size="icon"
        className="size-7"
        onClick={onReveal}
        disabled={revealing}
        title={revealed !== null ? t("hide") : t("reveal")}
      >
        {revealing ? (
          <IconLoader2 className="size-3 animate-spin" />
        ) : revealed !== null ? (
          <IconEyeOff className="size-4" />
        ) : (
          <IconEye className="size-4" />
        )}
      </Button>
      <SecretDialog
        mode="edit"
        initial={row}
        trigger={
          <Button
            variant="ghost"
            size="icon"
            className="size-7"
            title={t("edit")}
          >
            <IconEdit className="size-4" />
          </Button>
        }
      />
      <Button
        variant="ghost"
        size="icon"
        className="size-7 text-rose-600 hover:text-rose-700"
        onClick={onDelete}
        title={t("delete")}
      >
        <IconTrash className="size-4" />
      </Button>
    </div>
  );
}

function SecretDialog({
  mode,
  initial,
  trigger,
}: {
  mode: "create" | "edit";
  initial?: SecretRead;
  trigger?: React.ReactNode;
}) {
  const t = useTranslations("settings.secrets");
  const create = useCreateSecret();
  const update = useUpdateSecret(initial?.id ?? "");
  const [open, setOpen] = useState(false);
  const [name, setName] = useState(initial?.name ?? "");
  const [value, setValue] = useState("");
  const [kind, setKind] = useState(initial?.kind ?? "generic");
  const [requireApproval, setRequireApproval] = useState(
    initial?.required_approval ?? false,
  );
  const [description, setDescription] = useState(
    String((initial?.metadata_json as { description?: string })?.description ?? ""),
  );

  const reset = () => {
    setName(initial?.name ?? "");
    setValue("");
    setKind(initial?.kind ?? "generic");
    setRequireApproval(initial?.required_approval ?? false);
    setDescription(
      String((initial?.metadata_json as { description?: string })?.description ?? ""),
    );
  };

  const submit = async () => {
    try {
      if (mode === "create") {
        if (!name.trim() || !value.trim()) return;
        await create.mutateAsync({
          name: name.trim(),
          value,
          kind,
          required_approval: requireApproval,
          metadata_json: description.trim()
            ? { description: description.trim() }
            : {},
        });
      } else {
        // Edit: only send changed value if user typed one (avoids re-encrypting
        // unchanged secrets), but always send the metadata + flags so the user
        // can rotate description / approval requirement without rotating the
        // secret itself.
        const patch: Partial<{
          value: string;
          required_approval: boolean;
          metadata_json: Record<string, unknown>;
        }> = {
          required_approval: requireApproval,
          metadata_json: description.trim()
            ? { ...(initial?.metadata_json ?? {}), description: description.trim() }
            : { ...(initial?.metadata_json ?? {}), description: undefined },
        };
        if (value.trim()) patch.value = value;
        await update.mutateAsync(patch);
      }
      toast.success(t("saved"));
      setOpen(false);
      reset();
    } catch {
      toast.error(t("saveFailed"));
    }
  };

  const pending = create.isPending || update.isPending;

  return (
    <Dialog
      open={open}
      onOpenChange={(o) => {
        setOpen(o);
        if (!o) reset();
      }}
    >
      <DialogTrigger asChild>
        {trigger ?? (
          <Button size="sm">
            <IconPlus className="size-4" />
            {t("new")}
          </Button>
        )}
      </DialogTrigger>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>{mode === "create" ? t("dialogNew") : t("dialogEdit")}</DialogTitle>
          <DialogDescription>{t("dialogHint")}</DialogDescription>
        </DialogHeader>
        <div className="space-y-3">
          <div className="grid gap-1.5">
            <Label htmlFor="secret-name">{t("nameLabel")}</Label>
            <Input
              id="secret-name"
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="stripe_api_key"
              disabled={mode === "edit"}
            />
            {mode === "edit" && (
              <p className="text-[11px] sh-muted">{t("nameImmutableHint")}</p>
            )}
          </div>
          <div className="grid gap-1.5">
            <Label htmlFor="secret-kind">{t("kindLabel")}</Label>
            <Select value={kind} onValueChange={setKind}>
              <SelectTrigger id="secret-kind">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {KIND_OPTIONS.map((k) => (
                  <SelectItem key={k} value={k}>
                    {t(`kind.${k}`)}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
          <div className="grid gap-1.5">
            <Label htmlFor="secret-desc">{t("descriptionLabel")}</Label>
            <Input
              id="secret-desc"
              value={description}
              onChange={(e) => setDescription(e.target.value)}
              placeholder={t("descriptionPlaceholder")}
              maxLength={200}
            />
          </div>
          <div className="grid gap-1.5">
            <Label htmlFor="secret-value">
              {mode === "create" ? t("valueLabel") : t("rotateLabel")}
            </Label>
            <Textarea
              id="secret-value"
              value={value}
              onChange={(e) => setValue(e.target.value)}
              className="min-h-[100px] font-mono text-xs"
              placeholder={
                mode === "create"
                  ? t("valuePlaceholder")
                  : t("rotatePlaceholder")
              }
            />
          </div>
          <div className="flex items-center justify-between rounded-md border p-2.5">
            <div className="text-sm">{t("requiresApprovalToggle")}</div>
            <Switch
              checked={requireApproval}
              onCheckedChange={setRequireApproval}
            />
          </div>
        </div>
        <DialogFooter>
          <Button variant="ghost" onClick={() => setOpen(false)} disabled={pending}>
            {t("cancel")}
          </Button>
          <Button
            onClick={submit}
            disabled={
              pending ||
              (mode === "create" && (!name.trim() || !value.trim()))
            }
          >
            {pending && <IconLoader2 className="size-3 animate-spin" />}
            {t("save")}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
