"use client";

import { useMemo, useState } from "react";
import { useTranslations } from "next-intl";
import { toast } from "sonner";
import { IconLoader2 } from "@tabler/icons-react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import {
  useCreateMcpServer,
  type McpServerCreateInput,
  type McpTransport,
} from "@/hooks/use-mcp-servers";

interface FormState {
  name: string;
  slug: string;
  transport: McpTransport;
  url: string;
  command: string;
  args: string;
  authMode: "none" | "bearer" | "oauth";
  bearerToken: string;
  bearerRef: string;
  oauthClientId: string;
  oauthClientSecret: string;
  oauthClientSecretRef: string;
  oauthTokenUrl: string;
  oauthScopes: string;
  oauthRefreshGrace: string;
  keepaliveSeconds: string;
  requestTimeoutSeconds: string;
  maxConcurrent: string;
}

const INITIAL: FormState = {
  name: "",
  slug: "",
  transport: "stdio",
  url: "",
  command: "",
  args: "",
  authMode: "none",
  bearerToken: "",
  bearerRef: "",
  oauthClientId: "",
  oauthClientSecret: "",
  oauthClientSecretRef: "",
  oauthTokenUrl: "",
  oauthScopes: "",
  oauthRefreshGrace: "300",
  keepaliveSeconds: "30",
  requestTimeoutSeconds: "300",
  maxConcurrent: "4",
};

export function McpServerForm({ onSaved }: { onSaved?: () => void }) {
  const t = useTranslations("mcp");
  const [state, setState] = useState<FormState>(INITIAL);
  const create = useCreateMcpServer();

  const update = <K extends keyof FormState>(key: K, value: FormState[K]) =>
    setState((prev) => ({ ...prev, [key]: value }));

  const isHttp = state.transport === "sse" || state.transport === "streamable_http";
  const showOAuth = isHttp && state.authMode === "oauth";

  const submit = async () => {
    const payload = buildPayload(state);
    if (!payload) {
      toast.error(t("validationFailed"));
      return;
    }
    try {
      await create.mutateAsync(payload);
      toast.success(t("created"));
      setState(INITIAL);
      onSaved?.();
    } catch (e) {
      toast.error(e instanceof Error ? e.message : t("createFailed"));
    }
  };

  const validationMessage = useMemo(() => validate(state, t), [state, t]);

  return (
    <Card>
      <CardHeader>
        <CardTitle>{t("formTitle")}</CardTitle>
        <CardDescription>{t("formDescription")}</CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        <div className="grid gap-3 md:grid-cols-2">
          <Field label={t("nameLabel")} required>
            <Input
              value={state.name}
              onChange={(e) => update("name", e.target.value)}
              placeholder="GitHub MCP"
            />
          </Field>
          <Field label={t("slugLabel")} required>
            <Input
              value={state.slug}
              onChange={(e) => update("slug", e.target.value.toLowerCase())}
              placeholder="github-mcp"
              pattern="^[a-z][a-z0-9_-]{1,63}$"
            />
          </Field>
        </div>

        <Field label={t("transportLabel")} required>
          <Select
            value={state.transport}
            onValueChange={(v) => update("transport", v as McpTransport)}
          >
            <SelectTrigger>
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="stdio">{t("transportStdio")}</SelectItem>
              <SelectItem value="sse">{t("transportSse")}</SelectItem>
              <SelectItem value="streamable_http">
                {t("transportStreamableHttp")}
              </SelectItem>
            </SelectContent>
          </Select>
        </Field>

        {state.transport === "stdio" ? (
          <>
            <Field label={t("commandLabel")} required hint={t("commandHint")}>
              <Input
                value={state.command}
                onChange={(e) => update("command", e.target.value)}
                placeholder="npx @modelcontextprotocol/server-github"
              />
            </Field>
            <Field label={t("argsLabel")} hint={t("argsHint")}>
              <Textarea
                value={state.args}
                onChange={(e) => update("args", e.target.value)}
                placeholder="--token=<from-vault>"
                rows={2}
              />
            </Field>
          </>
        ) : (
          <Field label={t("urlLabel")} required>
            <Input
              type="url"
              value={state.url}
              onChange={(e) => update("url", e.target.value)}
              placeholder="https://mcp.example.com/sse"
            />
          </Field>
        )}

        {isHttp && (
          <Field label={t("authModeLabel")}>
            <Select
              value={state.authMode}
              onValueChange={(v) =>
                update("authMode", v as FormState["authMode"])
              }
            >
              <SelectTrigger>
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="none">{t("authModeNone")}</SelectItem>
                <SelectItem value="bearer">{t("authModeBearer")}</SelectItem>
                <SelectItem value="oauth">{t("authModeOAuth")}</SelectItem>
              </SelectContent>
            </Select>
          </Field>
        )}

        {isHttp && state.authMode === "bearer" && (
          <div className="grid gap-3 md:grid-cols-2">
            <Field label={t("bearerTokenLabel")} hint={t("bearerTokenHint")}>
              <Input
                type="password"
                value={state.bearerToken}
                onChange={(e) => update("bearerToken", e.target.value)}
              />
            </Field>
            <Field label={t("bearerRefLabel")} hint={t("bearerRefHint")}>
              <Input
                value={state.bearerRef}
                onChange={(e) => update("bearerRef", e.target.value)}
                placeholder="${vault://workspace/github_pat}"
              />
            </Field>
          </div>
        )}

        {showOAuth && (
          <Card className="bg-black/[0.02] dark:bg-white/[0.02]">
            <CardHeader>
              <CardTitle className="text-sm">{t("oauthSectionTitle")}</CardTitle>
              <CardDescription>{t("oauthSectionDescription")}</CardDescription>
            </CardHeader>
            <CardContent className="space-y-3">
              <div className="grid gap-3 md:grid-cols-2">
                <Field label={t("clientIdLabel")} required>
                  <Input
                    value={state.oauthClientId}
                    onChange={(e) => update("oauthClientId", e.target.value)}
                  />
                </Field>
                <Field label={t("tokenUrlLabel")} required>
                  <Input
                    type="url"
                    value={state.oauthTokenUrl}
                    onChange={(e) => update("oauthTokenUrl", e.target.value)}
                    placeholder="https://idp.example.com/oauth/token"
                  />
                </Field>
              </div>
              <div className="grid gap-3 md:grid-cols-2">
                <Field label={t("clientSecretLabel")} hint={t("clientSecretHint")}>
                  <Input
                    type="password"
                    value={state.oauthClientSecret}
                    onChange={(e) => update("oauthClientSecret", e.target.value)}
                  />
                </Field>
                <Field
                  label={t("clientSecretRefLabel")}
                  hint={t("clientSecretRefHint")}
                >
                  <Input
                    value={state.oauthClientSecretRef}
                    onChange={(e) =>
                      update("oauthClientSecretRef", e.target.value)
                    }
                    placeholder="${vault://workspace/...}"
                  />
                </Field>
              </div>
              <Field label={t("scopesLabel")} hint={t("scopesHint")}>
                <Input
                  value={state.oauthScopes}
                  onChange={(e) => update("oauthScopes", e.target.value)}
                  placeholder="read:tools call:tools"
                />
              </Field>
              <Field label={t("refreshGraceLabel")} hint={t("refreshGraceHint")}>
                <Input
                  type="number"
                  min={30}
                  max={3600}
                  value={state.oauthRefreshGrace}
                  onChange={(e) => update("oauthRefreshGrace", e.target.value)}
                />
              </Field>
            </CardContent>
          </Card>
        )}

        <div className="grid gap-3 md:grid-cols-3">
          <Field
            label={t("keepaliveSecondsLabel")}
            hint={t("keepaliveSecondsHint")}
          >
            <Input
              type="number"
              min={5}
              max={600}
              value={state.keepaliveSeconds}
              onChange={(e) => update("keepaliveSeconds", e.target.value)}
            />
          </Field>
          <Field
            label={t("requestTimeoutSecondsLabel")}
            hint={t("requestTimeoutSecondsHint")}
          >
            <Input
              type="number"
              min={30}
              max={1800}
              value={state.requestTimeoutSeconds}
              onChange={(e) => update("requestTimeoutSeconds", e.target.value)}
            />
          </Field>
          <Field label={t("maxConcurrentLabel")} hint={t("maxConcurrentHint")}>
            <Input
              type="number"
              min={1}
              max={32}
              value={state.maxConcurrent}
              onChange={(e) => update("maxConcurrent", e.target.value)}
            />
          </Field>
        </div>

        {validationMessage && (
          <p className="text-xs text-amber-600 dark:text-amber-400">
            {validationMessage}
          </p>
        )}

        <div className="flex justify-end">
          <Button
            onClick={submit}
            disabled={create.isPending || !!validationMessage}
          >
            {create.isPending && <IconLoader2 className="size-4 animate-spin" />}
            {t("createButton")}
          </Button>
        </div>
      </CardContent>
    </Card>
  );
}

function Field({
  label,
  required,
  hint,
  children,
}: {
  label: string;
  required?: boolean;
  hint?: string;
  children: React.ReactNode;
}) {
  return (
    <div className="grid gap-1.5">
      <Label>
        {label}
        {required && <span className="ml-0.5 text-red-500">*</span>}
      </Label>
      {children}
      {hint && <p className="text-[11px] sh-muted">{hint}</p>}
    </div>
  );
}

function buildPayload(state: FormState): McpServerCreateInput | null {
  if (!state.name.trim() || !state.slug.trim()) return null;

  const args = state.args
    .split(/\r?\n/)
    .map((s) => s.trim())
    .filter(Boolean);

  const auth_json: Record<string, unknown> = {};
  if (state.authMode === "bearer") {
    if (state.bearerToken.trim()) auth_json.bearer = state.bearerToken.trim();
    if (state.bearerRef.trim()) auth_json.bearer_ref = state.bearerRef.trim();
  }

  const auth_oauth =
    state.authMode === "oauth" &&
    state.oauthClientId.trim() &&
    state.oauthTokenUrl.trim()
      ? {
          client_id: state.oauthClientId.trim(),
          client_secret: state.oauthClientSecret.trim() || null,
          client_secret_ref: state.oauthClientSecretRef.trim() || null,
          token_url: state.oauthTokenUrl.trim(),
          scopes: state.oauthScopes
            .split(/\s+/)
            .map((s) => s.trim())
            .filter(Boolean),
          refresh_grace_seconds: Number(state.oauthRefreshGrace) || 300,
        }
      : undefined;

  return {
    name: state.name.trim(),
    slug: state.slug.trim(),
    transport: state.transport,
    url: state.transport === "stdio" ? null : state.url.trim() || null,
    command: state.transport === "stdio" ? state.command.trim() || null : null,
    args_json: args,
    auth_json,
    auth_oauth,
    metadata_json: {
      keepalive_seconds: Number(state.keepaliveSeconds) || 30,
      request_timeout_seconds: Number(state.requestTimeoutSeconds) || 300,
      max_concurrent: Number(state.maxConcurrent) || 4,
    },
  };
}

function validate(
  state: FormState,
  t: ReturnType<typeof useTranslations>,
): string | null {
  if (!state.name.trim()) return t("validation.missingName");
  if (!/^[a-z][a-z0-9_-]{1,63}$/.test(state.slug.trim()))
    return t("validation.slugFormat");
  if (state.transport === "stdio") {
    if (!state.command.trim()) return t("validation.missingCommand");
  } else {
    if (!state.url.trim()) return t("validation.missingUrl");
  }
  if (state.authMode === "oauth") {
    if (!state.oauthClientId.trim()) return t("validation.missingOAuthClientId");
    if (!state.oauthTokenUrl.trim()) return t("validation.missingOAuthTokenUrl");
    if (!state.oauthClientSecret.trim() && !state.oauthClientSecretRef.trim())
      return t("validation.missingOAuthSecret");
  }
  return null;
}
