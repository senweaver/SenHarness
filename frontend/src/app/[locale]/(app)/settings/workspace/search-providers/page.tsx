"use client";

import { useMemo, useState } from "react";
import { useTranslations } from "next-intl";
import { toast } from "sonner";
import {
  IconCheck,
  IconKey,
  IconLoader2,
  IconPlus,
  IconTrash,
} from "@tabler/icons-react";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Switch } from "@/components/ui/switch";
import { Skeleton } from "@/components/ui/skeleton";
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
  DialogTrigger,
} from "@/components/ui/dialog";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { PageHeader } from "@/components/ui/page-header";
import {
  useCreateSearchProvider,
  useDeleteSearchProvider,
  useSearchProviderCatalog,
  useSearchProviders,
  useUpdateSearchProvider,
  type SearchProviderRead,
} from "@/hooks/use-search-providers";

export default function SearchProvidersPage() {
  const t = useTranslations("settings.searchProviders");
  const tSettings = useTranslations("settings");
  const { data: providers = [], isLoading } = useSearchProviders();
  const { data: catalog = [] } = useSearchProviderCatalog();

  const remove = useDeleteSearchProvider();
  const [editing, setEditing] = useState<SearchProviderRead | null>(null);
  const [open, setOpen] = useState(false);

  const catalogByKind = useMemo(() => {
    const map = new Map<string, (typeof catalog)[number]>();
    for (const e of catalog) map.set(e.kind, e);
    return map;
  }, [catalog]);

  return (
    <div>
      <PageHeader
        title={t("title")}
        description={t("description")}
        actions={
          <Dialog
            open={open}
            onOpenChange={(v) => {
              setOpen(v);
              if (!v) setEditing(null);
            }}
          >
            <DialogTrigger asChild>
              <Button size="sm" onClick={() => setEditing(null)}>
                <IconPlus className="size-4" />
                {t("new")}
              </Button>
            </DialogTrigger>
            <SearchProviderDialog
              editing={editing}
              catalog={catalog}
              onSaved={() => {
                setOpen(false);
                setEditing(null);
              }}
            />
          </Dialog>
        }
      />

      {isLoading && <Skeleton className="h-20" />}

      {!isLoading && providers.length === 0 ? (
        <Card>
          <CardContent className="py-10 text-center text-sm sh-muted">
            {t("empty")}
          </CardContent>
        </Card>
      ) : null}

      <div className="grid gap-3 sm:grid-cols-2">
        {providers.map((p) => {
          const meta = catalogByKind.get(p.kind);
          return (
            <Card key={p.id}>
              <CardHeader>
                <div className="flex items-center gap-2">
                  <CardTitle className="flex-1 truncate">
                    {meta?.display_name_zh ?? p.name}
                  </CardTitle>
                  <Badge variant="outline">{p.kind}</Badge>
                  {p.enabled ? (
                    <Badge variant="success">
                      <IconCheck className="mr-0.5 size-3" /> on
                    </Badge>
                  ) : (
                    <Badge variant="default">off</Badge>
                  )}
                </div>
                <CardDescription>
                  {meta?.description_zh ??
                    meta?.description ??
                    p.base_url ??
                    ""}
                </CardDescription>
              </CardHeader>
              <CardContent className="flex items-center gap-2 pt-0">
                <Badge variant={p.has_key ? "primary" : "warning"}>
                  <IconKey className="mr-0.5 size-3" />
                  {p.has_key ? t("badges.keySaved") : t("badges.noKey")}
                </Badge>
                <Badge variant="outline" className="text-[10px]">
                  P{p.priority}
                </Badge>
                <div className="ml-auto flex gap-1">
                  <Button
                    variant="outline"
                    size="sm"
                    onClick={() => {
                      setEditing(p);
                      setOpen(true);
                    }}
                  >
                    {tSettings("edit")}
                  </Button>
                  <Button
                    variant="ghost"
                    size="sm"
                    onClick={async () => {
                      if (!confirm(tSettings("confirmDelete"))) return;
                      try {
                        await remove.mutateAsync(p.id);
                        toast.success(tSettings("deleted"));
                      } catch {
                        toast.error(tSettings("deleteFailed"));
                      }
                    }}
                  >
                    <IconTrash className="size-3.5" />
                  </Button>
                </div>
              </CardContent>
            </Card>
          );
        })}
      </div>
    </div>
  );
}

function SearchProviderDialog({
  editing,
  catalog,
  onSaved,
}: {
  editing: SearchProviderRead | null;
  catalog: ReturnType<typeof useSearchProviderCatalog>["data"];
  onSaved: () => void;
}) {
  const t = useTranslations("settings.searchProviders");
  const tSettings = useTranslations("settings");
  const create = useCreateSearchProvider();
  const update = useUpdateSearchProvider(editing?.id ?? "");

  const fallbackKind = catalog?.[0]?.kind ?? "tavily";
  const [kind, setKind] = useState<string>(editing?.kind ?? fallbackKind);
  const meta = (catalog ?? []).find((c) => c.kind === kind);
  const [name, setName] = useState(editing?.name ?? meta?.display_name_zh ?? "");
  const [baseUrl, setBaseUrl] = useState(
    editing?.base_url ?? meta?.default_base_url ?? "",
  );
  const [priority, setPriority] = useState<number>(editing?.priority ?? 100);
  const [enabled, setEnabled] = useState(editing?.enabled ?? true);
  const [apiKey, setApiKey] = useState("");

  const submit = async () => {
    try {
      if (editing) {
        await update.mutateAsync({
          name,
          base_url: baseUrl || null,
          enabled,
          priority,
          api_key: apiKey || null,
        });
        toast.success(tSettings("saved"));
      } else {
        await create.mutateAsync({
          kind,
          name: name || meta?.display_name || kind,
          base_url: baseUrl || null,
          priority,
          api_key: apiKey || null,
          enabled,
        });
        toast.success(tSettings("created"));
      }
      onSaved();
    } catch {
      toast.error(tSettings(editing ? "saveFailed" : "createFailed"));
    }
  };

  const submitting = create.isPending || update.isPending;

  return (
    <DialogContent>
      <DialogHeader>
        <DialogTitle>{editing ? tSettings("edit") : t("new")}</DialogTitle>
        <DialogDescription>{t("description")}</DialogDescription>
      </DialogHeader>

      <div className="space-y-3">
        <div className="grid gap-1.5">
          <Label htmlFor="kind">{t("fields.kind")}</Label>
          <Select
            value={kind}
            onValueChange={(v) => setKind(v)}
            disabled={!!editing}
          >
            <SelectTrigger>
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {(catalog ?? []).map((c) => (
                <SelectItem key={c.kind} value={c.kind}>
                  {c.display_name_zh ?? c.display_name}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>

        <div className="grid gap-1.5">
          <Label>{t("fields.name")}</Label>
          <Input
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder={meta?.display_name_zh ?? meta?.display_name ?? ""}
          />
        </div>

        <div className="grid gap-1.5">
          <Label>{t("fields.baseUrl")}</Label>
          <Input
            value={baseUrl}
            onChange={(e) => setBaseUrl(e.target.value)}
            placeholder={meta?.default_base_url ?? ""}
          />
        </div>

        <div className="grid gap-1.5">
          <Label>{t("fields.apiKey")}</Label>
          <Input
            type="password"
            value={apiKey}
            onChange={(e) => setApiKey(e.target.value)}
            placeholder={
              editing?.has_key
                ? t("fields.apiKeyPlaceholderSaved")
                : t("fields.apiKeyPlaceholder")
            }
            autoComplete="off"
          />
          {!meta?.needs_key ? (
            <p className="text-[11px] text-muted-foreground">
              {t("fields.noKeyNeeded")}
            </p>
          ) : null}
        </div>

        <div className="grid gap-1.5">
          <Label>{t("fields.priority")}</Label>
          <Input
            type="number"
            value={priority}
            onChange={(e) => setPriority(Number(e.target.value))}
          />
          <p className="text-[11px] text-muted-foreground">
            {t("fields.priorityHint")}
          </p>
        </div>

        <div className="flex items-center justify-between rounded-md border p-2">
          <Label>{t("fields.enabled")}</Label>
          <Switch checked={enabled} onCheckedChange={setEnabled} />
        </div>
      </div>

      <DialogFooter>
        <Button variant="ghost" onClick={onSaved} disabled={submitting}>
          {t("cancel")}
        </Button>
        <Button onClick={submit} disabled={submitting || !kind}>
          {submitting && <IconLoader2 className="size-4 animate-spin" />}
          {t("save")}
        </Button>
      </DialogFooter>
    </DialogContent>
  );
}
