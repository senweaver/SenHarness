"use client";

import { useEffect, useState } from "react";
import { useRouter } from "@/lib/navigation";
import { IconLoader2, IconTrash } from "@tabler/icons-react";
import { useTranslations } from "next-intl";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
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
import { useAgents } from "@/hooks/use-agents";
import {
  type FlowRead,
  type FlowTriggerKind,
  useCreateFlow,
  useDeleteFlow,
  useUpdateFlow,
} from "@/hooks/use-flows";

export function FlowForm({
  mode,
  initial,
}: {
  mode: "create" | "edit";
  initial?: FlowRead;
}) {
  const t = useTranslations("flows.form");
  const tFlows = useTranslations("flows");
  const tCommon = useTranslations("common");
  const tSettings = useTranslations("settings");
  const router = useRouter();

  const [name, setName] = useState(initial?.name ?? "");
  const [description, setDescription] = useState(initial?.description ?? "");
  const [triggerKind, setTriggerKind] = useState<FlowTriggerKind>(
    initial?.trigger_kind ?? "manual",
  );
  const [cronExpr, setCronExpr] = useState<string>(
    String((initial?.trigger_config as Record<string, unknown>)?.expr ?? "0 9 * * *"),
  );
  const [cronTz, setCronTz] = useState<string>(
    String(
      (initial?.trigger_config as Record<string, unknown>)?.tz ?? "Asia/Shanghai",
    ),
  );
  const [webhookToken, setWebhookToken] = useState<string>(
    String(
      (initial?.trigger_config as Record<string, unknown>)?.token ??
        Math.random().toString(36).slice(2, 18),
    ),
  );
  const [agentId, setAgentId] = useState<string>(initial?.agent_id ?? "");
  const [prompt, setPrompt] = useState(
    initial?.prompt_template ?? t("promptDefault"),
  );
  const [enabled, setEnabled] = useState<boolean>(initial?.enabled ?? true);

  const { data: agents } = useAgents();

  const create = useCreateFlow();
  // Hooks must run unconditionally (rules-of-hooks). The "" sentinel is
  // safe because we only ever invoke ``update.mutateAsync`` when ``initial``
  // is present (see submit() below).
  const update = useUpdateFlow(initial?.id ?? "");
  const remove = useDeleteFlow();

  useEffect(() => {
    if (!initial) return;
    setName(initial.name);
    setDescription(initial.description ?? "");
    setTriggerKind(initial.trigger_kind);
    setAgentId(initial.agent_id ?? "");
    setPrompt(initial.prompt_template);
    setEnabled(initial.enabled);
    const cfg = (initial.trigger_config ?? {}) as Record<string, unknown>;
    if (cfg.expr) setCronExpr(String(cfg.expr));
    if (cfg.tz) setCronTz(String(cfg.tz));
    if (cfg.token) setWebhookToken(String(cfg.token));
  }, [initial?.id]);

  const submit = async () => {
    if (!name.trim() || !agentId || !prompt.trim()) {
      toast.error(t("missingFields"));
      return;
    }
    const trigger_config =
      triggerKind === "cron"
        ? { expr: cronExpr.trim(), tz: cronTz.trim() || "UTC" }
        : triggerKind === "webhook"
          ? { token: webhookToken.trim() }
          : {};
    try {
      if (mode === "edit" && initial) {
        await update.mutateAsync({
          name,
          description: description || null,
          trigger_kind: triggerKind,
          trigger_config,
          agent_id: agentId,
          prompt_template: prompt,
          enabled,
        });
        toast.success(tSettings("saved"));
      } else {
        const created = await create.mutateAsync({
          name,
          description: description || null,
          trigger_kind: triggerKind,
          trigger_config,
          agent_id: agentId,
          prompt_template: prompt,
          enabled,
        });
        toast.success(tSettings("created"));
        router.push(`/flows/${created.id}`);
      }
    } catch {
      toast.error(tSettings(mode === "edit" ? "saveFailed" : "createFailed"));
    }
  };

  const onDelete = async () => {
    if (!initial) return;
    if (!confirm(tFlows("confirmDelete"))) return;
    try {
      await remove.mutateAsync(initial.id);
      toast.success(tSettings("deleted"));
      router.push("/flows");
    } catch {
      toast.error(tSettings("deleteFailed"));
    }
  };

  const submitting = create.isPending || update.isPending || remove.isPending;

  return (
    <div className="space-y-4">
      <Card>
        <CardHeader>
          <CardTitle>
            {mode === "create" ? tFlows("new") : tFlows("edit")}
          </CardTitle>
        </CardHeader>
        <CardContent className="space-y-3">
          <div className="grid gap-3 sm:grid-cols-2">
            <div className="grid gap-1.5">
              <Label>{t("name")}</Label>
              <Input
                data-testid="flow-form-name"
                value={name}
                onChange={(e) => setName(e.target.value)}
                placeholder={t("namePlaceholder")}
              />
            </div>
            <div className="grid gap-1.5">
              <Label>{t("agent")}</Label>
              <Select value={agentId} onValueChange={setAgentId}>
                <SelectTrigger>
                  <SelectValue placeholder={t("agentPlaceholder")} />
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
          </div>
          <div className="grid gap-1.5">
            <Label>{t("description")}</Label>
            <Input
              data-testid="flow-form-description"
              value={description}
              onChange={(e) => setDescription(e.target.value)}
              placeholder={t("descriptionPlaceholder")}
            />
          </div>

          <div className="grid gap-1.5">
            <Label>{t("trigger")}</Label>
            <Select
              value={triggerKind}
              onValueChange={(v) => setTriggerKind(v as FlowTriggerKind)}
            >
              <SelectTrigger>
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="manual">{t("triggerName.manual")}</SelectItem>
                <SelectItem value="cron">{t("triggerName.cron")}</SelectItem>
                <SelectItem value="webhook">
                  {t("triggerName.webhook")}
                </SelectItem>
              </SelectContent>
            </Select>
            <p className="text-[11px] sh-muted">
              {t(`triggerDesc.${triggerKind}`)}
            </p>
          </div>

          {triggerKind === "cron" && (
            <div className="grid gap-3 sm:grid-cols-2">
              <div className="grid gap-1.5">
                <Label>{t("cronExpr")}</Label>
                <Input
                  value={cronExpr}
                  onChange={(e) => setCronExpr(e.target.value)}
                  placeholder="0 9 * * *"
                  className="font-mono"
                />
                <p className="text-[11px] sh-muted">{t("cronExprHint")}</p>
              </div>
              <div className="grid gap-1.5">
                <Label>{t("cronTz")}</Label>
                <Input
                  value={cronTz}
                  onChange={(e) => setCronTz(e.target.value)}
                  placeholder="Asia/Shanghai"
                />
              </div>
            </div>
          )}

          {triggerKind === "webhook" && (
            <div className="grid gap-1.5">
              <Label>{t("webhookToken")}</Label>
              <Input
                value={webhookToken}
                onChange={(e) => setWebhookToken(e.target.value)}
                className="font-mono"
              />
              <p className="text-[11px] sh-muted">{t("webhookHint")}</p>
            </div>
          )}

          <div className="grid gap-1.5">
            <Label>{t("prompt")}</Label>
            <Textarea
              value={prompt}
              onChange={(e) => setPrompt(e.target.value)}
              className="min-h-[140px] font-mono text-[13px]"
              placeholder={t("promptPlaceholder")}
            />
            <p className="text-[11px] sh-muted">{t("promptHint")}</p>
          </div>

          <div className="flex items-center justify-between rounded-md border p-3">
            <div>
              <div className="text-sm font-medium">{t("enabled")}</div>
              <div className="text-[11px] sh-muted">{t("enabledHint")}</div>
            </div>
            <Switch checked={enabled} onCheckedChange={setEnabled} />
          </div>
        </CardContent>
      </Card>

      <div className="flex items-center justify-between">
        {mode === "edit" && initial ? (
          <Button
            variant="destructive"
            onClick={onDelete}
            disabled={submitting}
          >
            <IconTrash className="size-4" />
            {tCommon("delete")}
          </Button>
        ) : (
          <span />
        )}
        <div className="flex gap-2">
          <Button
            variant="ghost"
            onClick={() => router.back()}
            disabled={submitting}
          >
            {tCommon("cancel")}
          </Button>
          <Button
            data-testid="flow-form-submit"
            onClick={submit}
            disabled={submitting || !name.trim() || !agentId}
          >
            {submitting && <IconLoader2 className="size-4 animate-spin" />}
            {tCommon("save")}
          </Button>
        </div>
      </div>
    </div>
  );
}
