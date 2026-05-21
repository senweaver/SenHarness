"use client";

import { useEffect, useState } from "react";
import { useRouter } from "@/lib/navigation";
import { IconLoader2, IconPlayerPlay, IconPlus, IconTrash } from "@tabler/icons-react";
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
  type FlowExecutionMode,
  type FlowRead,
  type FlowTriggerKind,
  useCreateFlow,
  useDeleteFlow,
  useUpdateFlow,
} from "@/hooks/use-flows";
import { useFlowTestHttp, useFlowTestScript } from "@/hooks/use-flow-test";

type HttpMethod = "GET" | "HEAD" | "POST";

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
  const tMode = useTranslations("flowMode");
  const router = useRouter();

  const [name, setName] = useState(initial?.name ?? "");
  const [description, setDescription] = useState(initial?.description ?? "");
  const [executionMode, setExecutionMode] = useState<FlowExecutionMode>(
    initial?.execution_mode ?? "agent",
  );
  const [triggerKind, setTriggerKind] = useState<FlowTriggerKind>(
    initial?.trigger_kind ?? "manual",
  );
  const cfg = (initial?.trigger_config ?? {}) as Record<string, unknown>;
  const [cronExpr, setCronExpr] = useState<string>(
    String(cfg.expr ?? "0 9 * * *"),
  );
  const [cronTz, setCronTz] = useState<string>(String(cfg.tz ?? "Asia/Shanghai"));
  const [webhookToken, setWebhookToken] = useState<string>(
    String(cfg.token ?? Math.random().toString(36).slice(2, 18)),
  );
  const [scriptCommand, setScriptCommand] = useState<string>(
    String(cfg.script_command ?? ""),
  );
  const [scriptCwd, setScriptCwd] = useState<string>(
    String(cfg.script_cwd ?? ""),
  );
  const [scriptTimeout, setScriptTimeout] = useState<number>(
    Number(cfg.script_timeout_s ?? 60),
  );
  const [escalateScript, setEscalateScript] = useState<boolean>(
    cfg.escalate_on_nonempty_output === undefined
      ? true
      : Boolean(cfg.escalate_on_nonempty_output),
  );
  const [httpUrl, setHttpUrl] = useState<string>(String(cfg.http_url ?? ""));
  const [httpMethod, setHttpMethod] = useState<HttpMethod>(
    (cfg.http_method as HttpMethod) ?? "GET",
  );
  const [httpHeaders, setHttpHeaders] = useState<Array<[string, string]>>(
    Object.entries((cfg.http_headers ?? {}) as Record<string, string>),
  );
  const [httpBody, setHttpBody] = useState<string>(String(cfg.http_body ?? ""));
  const [httpTimeout, setHttpTimeout] = useState<number>(
    Number(cfg.http_timeout_s ?? 30),
  );
  const [escalateHttp, setEscalateHttp] = useState<boolean>(
    cfg.escalate_on_http_failure === undefined
      ? true
      : Boolean(cfg.escalate_on_http_failure),
  );
  const [agentId, setAgentId] = useState<string>(initial?.agent_id ?? "");
  const [prompt, setPrompt] = useState(
    initial?.prompt_template ?? t("promptDefault"),
  );
  const [enabled, setEnabled] = useState<boolean>(initial?.enabled ?? true);

  const { data: agents } = useAgents();

  const create = useCreateFlow();
  // Hooks must run unconditionally; the empty sentinel is safe because
  // ``update.mutateAsync`` is only invoked when ``initial`` is present.
  const update = useUpdateFlow(initial?.id ?? "");
  const remove = useDeleteFlow();
  const testScript = useFlowTestScript(initial?.id ?? "");
  const testHttp = useFlowTestHttp(initial?.id ?? "");

  useEffect(() => {
    if (!initial) return;
    setName(initial.name);
    setDescription(initial.description ?? "");
    setExecutionMode(initial.execution_mode);
    setTriggerKind(initial.trigger_kind);
    setAgentId(initial.agent_id ?? "");
    setPrompt(initial.prompt_template);
    setEnabled(initial.enabled);
    const c = (initial.trigger_config ?? {}) as Record<string, unknown>;
    if (c.expr) setCronExpr(String(c.expr));
    if (c.tz) setCronTz(String(c.tz));
    if (c.token) setWebhookToken(String(c.token));
    if (c.script_command !== undefined) setScriptCommand(String(c.script_command));
    if (c.script_cwd !== undefined) setScriptCwd(String(c.script_cwd ?? ""));
    if (c.script_timeout_s !== undefined)
      setScriptTimeout(Number(c.script_timeout_s));
    if (c.escalate_on_nonempty_output !== undefined)
      setEscalateScript(Boolean(c.escalate_on_nonempty_output));
    if (c.http_url !== undefined) setHttpUrl(String(c.http_url));
    if (c.http_method !== undefined) setHttpMethod(c.http_method as HttpMethod);
    if (c.http_headers !== undefined)
      setHttpHeaders(Object.entries(c.http_headers as Record<string, string>));
    if (c.http_body !== undefined) setHttpBody(String(c.http_body));
    if (c.http_timeout_s !== undefined) setHttpTimeout(Number(c.http_timeout_s));
    if (c.escalate_on_http_failure !== undefined)
      setEscalateHttp(Boolean(c.escalate_on_http_failure));
  }, [initial?.id]);

  const isAgent = executionMode === "agent";
  const isScript = executionMode === "no_agent_script";
  const isHttp = executionMode === "no_agent_http";

  const buildTriggerConfig = (): Record<string, unknown> => {
    const base: Record<string, unknown> = {};
    if (triggerKind === "cron") {
      base.expr = cronExpr.trim();
      base.tz = cronTz.trim() || "UTC";
    } else if (triggerKind === "webhook") {
      base.token = webhookToken.trim();
    }
    if (isScript) {
      base.script_command = scriptCommand.trim();
      if (scriptCwd.trim()) base.script_cwd = scriptCwd.trim();
      base.script_timeout_s = scriptTimeout;
      base.escalate_on_nonempty_output = escalateScript;
    } else if (isHttp) {
      base.http_url = httpUrl.trim();
      base.http_method = httpMethod;
      base.http_headers = Object.fromEntries(
        httpHeaders.filter(([k]) => k.trim().length > 0),
      );
      if (httpMethod === "POST" && httpBody.trim().length > 0) {
        base.http_body = httpBody;
      }
      base.http_timeout_s = httpTimeout;
      base.escalate_on_http_failure = escalateHttp;
    }
    return base;
  };

  const submit = async () => {
    if (!name.trim()) {
      toast.error(t("missingFields"));
      return;
    }
    if (isAgent && (!agentId || !prompt.trim())) {
      toast.error(t("missingFields"));
      return;
    }
    if (isScript && !scriptCommand.trim()) {
      toast.error(tMode("scriptCommandRequired"));
      return;
    }
    if (isHttp && !httpUrl.trim()) {
      toast.error(tMode("httpUrlRequired"));
      return;
    }
    const trigger_config = buildTriggerConfig();
    try {
      const payload = {
        name,
        description: description || null,
        trigger_kind: triggerKind,
        trigger_config,
        execution_mode: executionMode,
        agent_id: agentId || null,
        prompt_template: isAgent ? prompt : "",
        enabled,
      };
      if (mode === "edit" && initial) {
        await update.mutateAsync(payload);
        toast.success(tSettings("saved"));
      } else {
        const created = await create.mutateAsync(payload);
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

  const onTest = async () => {
    if (mode !== "edit" || !initial) {
      toast.error(tMode("saveBeforeTest"));
      return;
    }
    try {
      const override = buildTriggerConfig();
      const result = isScript
        ? await testScript.mutateAsync({ override })
        : isHttp
          ? await testHttp.mutateAsync({ override })
          : null;
      if (!result) return;
      toast.success(
        tMode("testRunFinished", {
          outcome: tMode(`outcome_${result.outcome}`),
          duration: result.duration_ms,
        }),
      );
    } catch {
      toast.error(tMode("testRunFailed"));
    }
  };

  const submitting = create.isPending || update.isPending || remove.isPending;
  const testing = testScript.isPending || testHttp.isPending;

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
              <Label>{tMode("modeLabel")}</Label>
              <Select
                value={executionMode}
                onValueChange={(v) => setExecutionMode(v as FlowExecutionMode)}
              >
                <SelectTrigger>
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="agent">{tMode("modeAgent")}</SelectItem>
                  <SelectItem value="no_agent_script">
                    {tMode("modeNoAgentScript")}
                  </SelectItem>
                  <SelectItem value="no_agent_http">
                    {tMode("modeNoAgentHttp")}
                  </SelectItem>
                </SelectContent>
              </Select>
              <p className="text-[11px] sh-muted">
                {tMode(`modeDesc_${executionMode}`)}
              </p>
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

          {isAgent && (
            <>
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
            </>
          )}

          {isScript && (
            <div className="space-y-3">
              <div className="grid gap-1.5">
                <Label>{tMode("scriptCommandLabel")}</Label>
                <Textarea
                  value={scriptCommand}
                  onChange={(e) => setScriptCommand(e.target.value)}
                  className="min-h-[100px] font-mono text-[13px]"
                  placeholder={tMode("scriptCommandPlaceholder")}
                />
                <p className="text-[11px] sh-muted">
                  {tMode("scriptCommandHint")}
                </p>
              </div>
              <div className="grid gap-3 sm:grid-cols-2">
                <div className="grid gap-1.5">
                  <Label>{tMode("scriptCwdLabel")}</Label>
                  <Input
                    value={scriptCwd}
                    onChange={(e) => setScriptCwd(e.target.value)}
                    placeholder="/workspace"
                  />
                </div>
                <div className="grid gap-1.5">
                  <Label>{tMode("scriptTimeoutLabel")}</Label>
                  <Input
                    type="number"
                    value={scriptTimeout}
                    min={1}
                    max={600}
                    onChange={(e) =>
                      setScriptTimeout(parseInt(e.target.value, 10) || 60)
                    }
                  />
                </div>
              </div>
              <div className="flex items-center justify-between rounded-md border p-3">
                <div>
                  <div className="text-sm font-medium">
                    {tMode("escalateOnNonemptyOutput")}
                  </div>
                  <div className="text-[11px] sh-muted">
                    {tMode("escalateOnNonemptyOutputHint")}
                  </div>
                </div>
                <Switch
                  checked={escalateScript}
                  onCheckedChange={setEscalateScript}
                />
              </div>
              {escalateScript && (
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
              )}
            </div>
          )}

          {isHttp && (
            <div className="space-y-3">
              <div className="grid gap-3 sm:grid-cols-[1fr_140px]">
                <div className="grid gap-1.5">
                  <Label>{tMode("httpUrlLabel")}</Label>
                  <Input
                    value={httpUrl}
                    onChange={(e) => setHttpUrl(e.target.value)}
                    placeholder="https://example.com/health"
                    className="font-mono"
                  />
                  <p className="text-[11px] sh-muted">
                    {tMode("httpUrlHint")}
                  </p>
                </div>
                <div className="grid gap-1.5">
                  <Label>{tMode("httpMethodLabel")}</Label>
                  <Select
                    value={httpMethod}
                    onValueChange={(v) => setHttpMethod(v as HttpMethod)}
                  >
                    <SelectTrigger>
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                      <SelectItem value="GET">GET</SelectItem>
                      <SelectItem value="HEAD">HEAD</SelectItem>
                      <SelectItem value="POST">POST</SelectItem>
                    </SelectContent>
                  </Select>
                </div>
              </div>

              <div className="grid gap-1.5">
                <Label>{tMode("httpHeadersLabel")}</Label>
                <p className="text-[11px] sh-muted">
                  {tMode("httpHeadersHint")}
                </p>
                <div className="space-y-2">
                  {httpHeaders.map(([key, value], idx) => (
                    <div key={idx} className="grid gap-2 sm:grid-cols-[1fr_2fr_auto]">
                      <Input
                        value={key}
                        onChange={(e) => {
                          const next = [...httpHeaders];
                          next[idx] = [e.target.value, value];
                          setHttpHeaders(next);
                        }}
                        placeholder="Authorization"
                      />
                      <Input
                        value={value}
                        onChange={(e) => {
                          const next = [...httpHeaders];
                          next[idx] = [key, e.target.value];
                          setHttpHeaders(next);
                        }}
                        placeholder="Bearer ${vault://workspace/api_key}"
                        className="font-mono text-[12px]"
                      />
                      <Button
                        size="sm"
                        variant="ghost"
                        onClick={() =>
                          setHttpHeaders(httpHeaders.filter((_, i) => i !== idx))
                        }
                      >
                        <IconTrash className="size-4" />
                      </Button>
                    </div>
                  ))}
                  <Button
                    size="sm"
                    variant="outline"
                    onClick={() =>
                      setHttpHeaders([...httpHeaders, ["", ""]])
                    }
                  >
                    <IconPlus className="size-4" />
                    {tMode("addHeader")}
                  </Button>
                </div>
              </div>

              {httpMethod === "POST" && (
                <div className="grid gap-1.5">
                  <Label>{tMode("httpBodyLabel")}</Label>
                  <Textarea
                    value={httpBody}
                    onChange={(e) => setHttpBody(e.target.value)}
                    className="min-h-[80px] font-mono text-[13px]"
                  />
                </div>
              )}

              <div className="grid gap-3 sm:grid-cols-2">
                <div className="grid gap-1.5">
                  <Label>{tMode("httpTimeoutLabel")}</Label>
                  <Input
                    type="number"
                    value={httpTimeout}
                    min={1}
                    max={120}
                    onChange={(e) =>
                      setHttpTimeout(parseInt(e.target.value, 10) || 30)
                    }
                  />
                </div>
                <div className="flex items-center justify-between rounded-md border p-3">
                  <div>
                    <div className="text-sm font-medium">
                      {tMode("escalateOnHttpFailure")}
                    </div>
                    <div className="text-[11px] sh-muted">
                      {tMode("escalateOnHttpFailureHint")}
                    </div>
                  </div>
                  <Switch
                    checked={escalateHttp}
                    onCheckedChange={setEscalateHttp}
                  />
                </div>
              </div>

              {escalateHttp && (
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
              )}
            </div>
          )}

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
          {(isScript || isHttp) && mode === "edit" && (
            <Button
              variant="outline"
              onClick={onTest}
              disabled={submitting || testing}
            >
              {testing ? (
                <IconLoader2 className="size-4 animate-spin" />
              ) : (
                <IconPlayerPlay className="size-4" />
              )}
              {tMode("testButtonLabel")}
            </Button>
          )}
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
            disabled={submitting || !name.trim()}
          >
            {submitting && <IconLoader2 className="size-4 animate-spin" />}
            {tCommon("save")}
          </Button>
        </div>
      </div>
    </div>
  );
}
