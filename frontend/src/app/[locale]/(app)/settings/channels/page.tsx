"use client";

import { useState } from "react";
import {
  IconCopy,
  IconLoader2,
  IconPlugConnected,
  IconPlus,
  IconRefresh,
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
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Switch } from "@/components/ui/switch";
import { Textarea } from "@/components/ui/textarea";
import { PageHeader } from "@/components/ui/page-header";
import { Skeleton } from "@/components/ui/skeleton";
import { useAgents } from "@/hooks/use-agents";
import {
  type ChannelKind,
  type ChannelRead,
  useChannels,
  useCreateChannel,
  useDeleteChannel,
  useRotateChannelToken,
  useUpdateChannel,
} from "@/hooks/use-channels";
import { API_BASE_URL } from "@/lib/api";
import { relativeTime } from "@/lib/utils";

export default function ChannelsPage() {
  const t = useTranslations("settings.channels");
  const tCommon = useTranslations("common");
  const { data, isLoading } = useChannels();
  const [creating, setCreating] = useState(false);

  return (
    <div>
      <PageHeader
        title={t("title")}
        description={t("description")}
        actions={
          <Button size="sm" onClick={() => setCreating((x) => !x)}>
            <IconPlus className="size-4" />
            {creating ? tCommon("cancel") : t("new")}
          </Button>
        }
      />

      {creating && (
        <Card className="mb-3">
          <CardContent className="py-4">
            <CreateChannelForm onDone={() => setCreating(false)} />
          </CardContent>
        </Card>
      )}

      {isLoading && <Skeleton className="h-40" />}

      {!isLoading && (data ?? []).length === 0 && !creating && (
        <Card>
          <CardContent className="py-10 text-center text-sm sh-muted">
            {t("empty")}
          </CardContent>
        </Card>
      )}

      <div className="flex flex-col gap-2">
        {(data ?? []).map((ch) => (
          <ChannelCard key={ch.id} ch={ch} />
        ))}
      </div>
    </div>
  );
}

function CreateChannelForm({ onDone }: { onDone: () => void }) {
  const t = useTranslations("settings.channels");
  const tCommon = useTranslations("common");
  const { data: agents } = useAgents();
  const create = useCreateChannel();

  const [name, setName] = useState("");
  const [kind, setKind] = useState<ChannelKind>("slack");
  const [agentId, setAgentId] = useState<string>("");
  const [botToken, setBotToken] = useState("");
  const [signingSecret, setSigningSecret] = useState("");

  const submit = async () => {
    if (!name.trim() || !agentId) {
      toast.error(t("missingFields"));
      return;
    }
    try {
      const config: Record<string, string> = {};
      if (botToken.trim()) config.bot_token = botToken.trim();
      if (signingSecret.trim()) config.signing_secret = signingSecret.trim();
      await create.mutateAsync({
        name: name.trim(),
        kind,
        config_json: config,
        default_agent_id: agentId,
        enabled: true,
      });
      toast.success(t("created"));
      onDone();
    } catch {
      toast.error(t("createFailed"));
    }
  };

  return (
    <div className="grid gap-3 sm:grid-cols-2">
      <div className="grid gap-1.5">
        <Label>{t("form.name")}</Label>
        <Input
          value={name}
          onChange={(e) => setName(e.target.value)}
          placeholder={t("form.namePlaceholder")}
        />
      </div>
      <div className="grid gap-1.5">
        <Label>{t("form.kind")}</Label>
        <Select value={kind} onValueChange={(v) => setKind(v as ChannelKind)}>
          <SelectTrigger>
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="slack">Slack</SelectItem>
            <SelectItem value="feishu">{t("kind.feishu")}</SelectItem>
            <SelectItem value="discord">Discord</SelectItem>
            <SelectItem value="webhook">{t("kind.webhook")}</SelectItem>
          </SelectContent>
        </Select>
      </div>
      <div className="grid gap-1.5 sm:col-span-2">
        <Label>{t("form.agent")}</Label>
        <Select value={agentId} onValueChange={setAgentId}>
          <SelectTrigger>
            <SelectValue placeholder={t("form.agentPlaceholder")} />
          </SelectTrigger>
          <SelectContent>
            {(agents ?? []).map((a) => (
              <SelectItem key={a.id} value={a.id}>
                {a.name}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      </div>

      {(kind === "slack" || kind === "discord") && (
        <div className="grid gap-1.5 sm:col-span-2">
          <Label>{t("form.botToken")}</Label>
          <Input
            value={botToken}
            onChange={(e) => setBotToken(e.target.value)}
            placeholder={
              kind === "slack" ? "xoxb-…" : t("form.botTokenPlaceholder")
            }
            type="password"
          />
          <p className="text-[11px] sh-muted">{t("form.botTokenHint")}</p>
        </div>
      )}
      {kind === "slack" && (
        <div className="grid gap-1.5 sm:col-span-2">
          <Label>{t("form.signingSecret")}</Label>
          <Input
            value={signingSecret}
            onChange={(e) => setSigningSecret(e.target.value)}
            type="password"
          />
        </div>
      )}

      <div className="mt-1 flex justify-end gap-2 sm:col-span-2">
        <Button variant="ghost" onClick={onDone} disabled={create.isPending}>
          {tCommon("cancel")}
        </Button>
        <Button onClick={submit} disabled={create.isPending}>
          {create.isPending && <IconLoader2 className="size-4 animate-spin" />}
          {tCommon("save")}
        </Button>
      </div>
    </div>
  );
}

function ChannelCard({ ch }: { ch: ChannelRead }) {
  const t = useTranslations("settings.channels");
  const tCommon = useTranslations("common");
  const { data: agents } = useAgents();
  const update = useUpdateChannel(ch.id);
  const remove = useDeleteChannel();
  const rotate = useRotateChannelToken(ch.id);

  const bound = (agents ?? []).find((a) => a.id === ch.default_agent_id);
  const webhookUrl = `${API_BASE_URL}/api/v1/hooks/ingress/${ch.id}?token=${ch.inbound_token}`;

  const copyUrl = async () => {
    try {
      await navigator.clipboard.writeText(webhookUrl);
      toast.success(t("urlCopied"));
    } catch {
      toast.error(tCommon("empty"));
    }
  };

  const onToggle = async (enabled: boolean) => {
    try {
      await update.mutateAsync({ enabled });
    } catch {
      toast.error(t("updateFailed"));
    }
  };

  const onRotate = async () => {
    if (!confirm(t("rotateConfirm"))) return;
    try {
      await rotate.mutateAsync();
      toast.success(t("rotated"));
    } catch {
      toast.error(t("updateFailed"));
    }
  };

  const onDelete = async () => {
    if (!confirm(t("deleteConfirm"))) return;
    try {
      await remove.mutateAsync(ch.id);
      toast.success(t("deleted"));
    } catch {
      toast.error(t("updateFailed"));
    }
  };

  return (
    <Card>
      <CardHeader className="pb-2">
        <div className="flex items-center gap-2">
          <IconPlugConnected className="size-4 text-[rgb(var(--color-primary))]" />
          <CardTitle className="flex-1 truncate text-base">{ch.name}</CardTitle>
          <Badge variant="outline">{ch.kind}</Badge>
          <Switch checked={ch.enabled} onCheckedChange={onToggle} />
        </div>
        <CardDescription className="text-[11px]">
          {t("bound")}: {bound?.name ?? "—"} · {relativeTime(ch.updated_at)}
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-2">
        <div>
          <Label className="text-[11px] sh-muted">{t("webhookUrl")}</Label>
          <div className="flex items-center gap-1">
            <Input
              readOnly
              value={webhookUrl}
              className="font-mono text-[11px]"
            />
            <Button
              size="icon"
              variant="outline"
              className="size-8"
              onClick={copyUrl}
              title={t("copyUrl")}
            >
              <IconCopy className="size-3.5" />
            </Button>
            <Button
              size="icon"
              variant="outline"
              className="size-8"
              onClick={onRotate}
              disabled={rotate.isPending}
              title={t("rotateToken")}
            >
              <IconRefresh className="size-3.5" />
            </Button>
            <Button
              size="icon"
              variant="destructive"
              className="size-8"
              onClick={onDelete}
              disabled={remove.isPending}
              title={tCommon("delete")}
            >
              <IconTrash className="size-3.5" />
            </Button>
          </div>
          <p className="mt-1 text-[10px] sh-muted">
            {t(`setupHint.${ch.kind}`)}
          </p>
        </div>
      </CardContent>
    </Card>
  );
}
