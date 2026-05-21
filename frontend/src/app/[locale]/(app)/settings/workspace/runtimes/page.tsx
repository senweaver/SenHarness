"use client";

import { useState } from "react";
import { useTranslations } from "next-intl";
import { toast } from "sonner";
import {
  IconCopy,
  IconHeartbeat,
  IconKey,
  IconLoader2,
  IconPlus,
  IconRefresh,
  IconTrash,
} from "@tabler/icons-react";

import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
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
import { PageHeader } from "@/components/ui/page-header";
import { CompareRuntimesCard } from "@/components/runtimes/CompareRuntimesCard";
import { RegisteredRuntimesCard } from "@/components/runtimes/RegisteredRuntimesCard";
import {
  useBackendAdapters,
  useCreateBackendAdapter,
  useDeleteBackendAdapter,
  usePingBackendAdapter,
  useRotateBackendAdapterKey,
  type BackendAdapterRead,
} from "@/hooks/use-backend-adapters";

type HealthKind = BackendAdapterRead["health_status"];

const HEALTH_STYLES: Record<
  HealthKind,
  { badge: "success" | "warning" | "default" | "danger"; label: string }
> = {
  healthy: { badge: "success", label: "healthy" },
  degraded: { badge: "warning", label: "degraded" },
  down: { badge: "danger", label: "down" },
  unknown: { badge: "default", label: "—" },
};

export default function RuntimesSettingsPage() {
  const t = useTranslations("settings.runtimes");
  const tSettings = useTranslations("settings");
  const { data, isLoading } = useBackendAdapters();
  const remove = useDeleteBackendAdapter();

  const [open, setOpen] = useState(false);
  const [revealed, setRevealed] = useState<{ name: string; key: string } | null>(
    null,
  );

  return (
    <div className="space-y-6">
      <PageHeader
        title={t("title")}
        description={t("description")}
        actions={
          <Dialog open={open} onOpenChange={setOpen}>
            <DialogTrigger asChild>
              <Button size="sm">
                <IconPlus className="size-4" />
                {t("new")}
              </Button>
            </DialogTrigger>
            <CreateAdapterDialog
              onSaved={(payload) => {
                setRevealed(payload);
                setOpen(false);
              }}
            />
          </Dialog>
        }
      />

      <RegisteredRuntimesCard />

      <section className="space-y-3">
        <h2 className="text-sm font-semibold">{t("adaptersHeading")}</h2>
        {isLoading && <Skeleton className="h-24" />}

        {!isLoading && (data ?? []).length === 0 && (
          <Card>
            <CardContent className="py-10 text-center text-sm sh-muted">
              {t("empty")}
            </CardContent>
          </Card>
        )}

        <div className="grid gap-3 sm:grid-cols-2">
          {(data ?? []).map((a) => (
            <AdapterCard
              key={a.id}
              adapter={a}
              onRotated={(payload) => setRevealed(payload)}
              onDelete={async () => {
                if (!confirm(tSettings("confirmDelete"))) return;
                try {
                  await remove.mutateAsync(a.id);
                  toast.success(tSettings("deleted"));
                } catch {
                  toast.error(tSettings("deleteFailed"));
                }
              }}
            />
          ))}
        </div>
      </section>

      <CompareRuntimesCard />

      <RevealedKeyDialog
        revealed={revealed}
        onClose={() => setRevealed(null)}
      />
    </div>
  );
}

function AdapterCard({
  adapter,
  onRotated,
  onDelete,
}: {
  adapter: BackendAdapterRead;
  onRotated: (p: { name: string; key: string }) => void;
  onDelete: () => Promise<void> | void;
}) {
  const t = useTranslations("settings.runtimes");
  const rotate = useRotateBackendAdapterKey(adapter.id);
  const ping = usePingBackendAdapter(adapter.id);

  const health = HEALTH_STYLES[adapter.health_status];
  const lastSeen = adapter.last_seen_at
    ? new Date(adapter.last_seen_at).toLocaleString()
    : t("neverSeen");

  return (
    <Card>
      <CardHeader>
        <div className="flex items-center gap-2">
          <CardTitle className="flex-1 truncate">{adapter.name}</CardTitle>
          <Badge variant="outline">{adapter.kind}</Badge>
          <Badge variant={health.badge}>{health.label}</Badge>
        </div>
        <CardDescription>
          {adapter.endpoint || t("noEndpoint")} · {t("lastSeen")}: {lastSeen}
        </CardDescription>
      </CardHeader>
      <CardContent className="flex items-center gap-2 pt-0">
        <Badge variant={adapter.enabled ? "primary" : "default"}>
          {adapter.enabled ? t("enabled") : t("disabled")}
        </Badge>
        <div className="ml-auto flex gap-1">
          <Button
            variant="outline"
            size="sm"
            onClick={async () => {
              try {
                const r = await ping.mutateAsync();
                toast.success(`${r.status} — ${r.detail ?? ""}`);
              } catch {
                toast.error(t("pingFailed"));
              }
            }}
            disabled={ping.isPending}
          >
            {ping.isPending ? (
              <IconLoader2 className="size-4 animate-spin" />
            ) : (
              <IconHeartbeat className="size-4" />
            )}
            {t("ping")}
          </Button>
          <Button
            variant="outline"
            size="sm"
            onClick={async () => {
              if (!confirm(t("confirmRotate"))) return;
              try {
                const r = await rotate.mutateAsync();
                onRotated({ name: adapter.name, key: r.api_key });
                toast.success(t("rotated"));
              } catch {
                toast.error(t("rotateFailed"));
              }
            }}
            disabled={rotate.isPending}
          >
            {rotate.isPending ? (
              <IconLoader2 className="size-4 animate-spin" />
            ) : (
              <IconRefresh className="size-4" />
            )}
            {t("rotate")}
          </Button>
          <Button variant="ghost" size="sm" onClick={onDelete}>
            <IconTrash className="size-3.5" />
          </Button>
        </div>
      </CardContent>
    </Card>
  );
}

function CreateAdapterDialog({
  onSaved,
}: {
  onSaved: (payload: { name: string; key: string }) => void;
}) {
  const t = useTranslations("settings.runtimes");
  const tSettings = useTranslations("settings");
  const create = useCreateBackendAdapter();
  const [name, setName] = useState("");
  const [endpoint, setEndpoint] = useState("");

  const submit = async () => {
    try {
      const res = await create.mutateAsync({
        name: name.trim(),
        kind: "openclaw",
        endpoint: endpoint.trim() || null,
      });
      toast.success(tSettings("created"));
      onSaved({ name: res.adapter.name, key: res.api_key });
      setName("");
      setEndpoint("");
    } catch {
      toast.error(tSettings("createFailed"));
    }
  };

  return (
    <DialogContent>
      <DialogHeader>
        <DialogTitle>{t("new")}</DialogTitle>
        <DialogDescription>{t("newHint")}</DialogDescription>
      </DialogHeader>
      <div className="space-y-3">
        <div className="grid gap-1.5">
          <Label htmlFor="name">{t("name")}</Label>
          <Input
            id="name"
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder="prod-openclaw"
          />
        </div>
        <div className="grid gap-1.5">
          <Label htmlFor="endpoint">{t("endpoint")}</Label>
          <Input
            id="endpoint"
            value={endpoint}
            onChange={(e) => setEndpoint(e.target.value)}
            placeholder="https://worker.example.com/"
          />
          <p className="text-[11px] sh-muted">{t("endpointHint")}</p>
        </div>
      </div>
      <DialogFooter>
        <Button
          onClick={submit}
          disabled={create.isPending || !name.trim()}
        >
          {create.isPending && <IconLoader2 className="size-4 animate-spin" />}
          {t("create")}
        </Button>
      </DialogFooter>
    </DialogContent>
  );
}

function RevealedKeyDialog({
  revealed,
  onClose,
}: {
  revealed: { name: string; key: string } | null;
  onClose: () => void;
}) {
  const t = useTranslations("settings.runtimes");

  return (
    <Dialog open={!!revealed} onOpenChange={(v) => !v && onClose()}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>
            <IconKey className="mr-1 inline size-4" />
            {t("keyRevealedTitle")}
          </DialogTitle>
          <DialogDescription>{t("keyRevealedHint")}</DialogDescription>
        </DialogHeader>
        {revealed && (
          <div className="space-y-3">
            <div className="rounded-md border bg-black/5 p-3 dark:bg-white/5">
              <div className="mb-1 text-[11px] sh-muted">
                {t("adapter")}: <span className="font-mono">{revealed.name}</span>
              </div>
              <code className="block break-all font-mono text-[12px]">
                {revealed.key}
              </code>
            </div>
            <Button
              variant="outline"
              size="sm"
              onClick={async () => {
                try {
                  await navigator.clipboard.writeText(revealed.key);
                  toast.success(t("copied"));
                } catch {
                  toast.error(t("copyFailed"));
                }
              }}
            >
              <IconCopy className="size-4" />
              {t("copy")}
            </Button>
          </div>
        )}
        <DialogFooter>
          <Button variant="ghost" onClick={onClose}>
            {t("dismiss")}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
