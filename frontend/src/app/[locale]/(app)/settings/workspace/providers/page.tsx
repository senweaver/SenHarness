"use client";

import { useState } from "react";
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
  useCreateProvider,
  useDeleteProvider,
  useProviders,
  useUpdateProvider,
  type ProviderKind,
  type ProviderRead,
} from "@/hooks/use-providers";

const KINDS: ProviderKind[] = [
  "openai",
  "anthropic",
  "google",
  "openrouter",
  "azure_openai",
  "deepseek",
  "moonshot",
  "groq",
  "ollama",
  "vllm",
  "sglang",
  "custom",
];

export default function ProvidersSettingsPage() {
  const t = useTranslations("settings.providers");
  const tSettings = useTranslations("settings");
  const { data, isLoading } = useProviders();
  const remove = useDeleteProvider();
  const [editing, setEditing] = useState<ProviderRead | null>(null);
  const [open, setOpen] = useState(false);

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
            <ProviderDialog
              editing={editing}
              onSaved={() => {
                setOpen(false);
                setEditing(null);
              }}
            />
          </Dialog>
        }
      />

      {isLoading && <Skeleton className="h-20" />}

      {!isLoading && (data ?? []).length === 0 && (
        <Card>
          <CardContent className="py-10 text-center text-sm sh-muted">{t("empty")}</CardContent>
        </Card>
      )}

      <div className="grid gap-3 sm:grid-cols-2">
        {(data ?? []).map((p) => (
          <Card key={p.id}>
            <CardHeader>
              <div className="flex items-center gap-2">
                <CardTitle className="flex-1 truncate">{p.name}</CardTitle>
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
                {p.base_url ? p.base_url : "default base_url"} · model:{" "}
                {p.default_model ?? "—"}
              </CardDescription>
            </CardHeader>
            <CardContent className="flex items-center gap-2 pt-0">
              <Badge variant={p.has_key ? "primary" : "warning"}>
                <IconKey className="mr-0.5 size-3" />
                {p.has_key ? "key saved" : "no key"}
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
                  edit
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
        ))}
      </div>
    </div>
  );
}

function ProviderDialog({
  editing,
  onSaved,
}: {
  editing: ProviderRead | null;
  onSaved: () => void;
}) {
  const t = useTranslations("settings.providers");
  const tSettings = useTranslations("settings");
  const create = useCreateProvider();
  const update = useUpdateProvider(editing?.id ?? "");

  const [kind, setKind] = useState<ProviderKind>(editing?.kind ?? "deepseek");
  const [name, setName] = useState(editing?.name ?? "");
  const [baseUrl, setBaseUrl] = useState(editing?.base_url ?? "");
  const [defaultModel, setDefaultModel] = useState(editing?.default_model ?? "");
  const [enabled, setEnabled] = useState(editing?.enabled ?? true);
  const [apiKey, setApiKey] = useState("");

  const submit = async () => {
    try {
      if (editing) {
        await update.mutateAsync({
          name,
          base_url: baseUrl || null,
          default_model: defaultModel || null,
          enabled,
          api_key: apiKey || null,
        });
        toast.success(tSettings("saved"));
      } else {
        await create.mutateAsync({
          kind,
          name: name || kind,
          base_url: baseUrl || null,
          default_model: defaultModel || null,
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
        <DialogTitle>{editing ? t("save") : t("new")}</DialogTitle>
        <DialogDescription>{t("description")}</DialogDescription>
      </DialogHeader>

      <div className="space-y-3">
        <div className="grid gap-1.5">
          <Label htmlFor="kind">{t("kind")}</Label>
          <Select value={kind} onValueChange={(v) => setKind(v as ProviderKind)} disabled={!!editing}>
            <SelectTrigger>
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {KINDS.map((k) => (
                <SelectItem key={k} value={k}>
                  {k}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>

        <div className="grid gap-1.5">
          <Label htmlFor="name">{t("name")}</Label>
          <Input id="name" value={name} onChange={(e) => setName(e.target.value)} placeholder={kind} />
        </div>

        <div className="grid gap-1.5">
          <Label htmlFor="base">{t("baseUrl")}</Label>
          <Input
            id="base"
            value={baseUrl}
            onChange={(e) => setBaseUrl(e.target.value)}
            placeholder="https://api.deepseek.com/v1"
          />
        </div>

        <div className="grid gap-1.5">
          <Label htmlFor="model">{t("defaultModel")}</Label>
          <Input
            id="model"
            value={defaultModel}
            onChange={(e) => setDefaultModel(e.target.value)}
            placeholder="deepseek-chat"
          />
        </div>

        <div className="grid gap-1.5">
          <Label htmlFor="key">{t("apiKey")}</Label>
          <Input
            id="key"
            type="password"
            value={apiKey}
            onChange={(e) => setApiKey(e.target.value)}
            placeholder={editing?.has_key ? "••• (leave empty to keep)" : t("apiKeyPlaceholder")}
            autoComplete="off"
          />
        </div>

        <div className="flex items-center justify-between rounded-md border p-2">
          <Label htmlFor="enabled">{t("enabled")}</Label>
          <Switch id="enabled" checked={enabled} onCheckedChange={setEnabled} />
        </div>
      </div>

      <DialogFooter>
        <Button variant="ghost" onClick={onSaved} disabled={submitting}>
          {t("cancel")}
        </Button>
        <Button onClick={submit} disabled={submitting || !name.trim()}>
          {submitting && <IconLoader2 className="size-4 animate-spin" />}
          {t("save")}
        </Button>
      </DialogFooter>
    </DialogContent>
  );
}
