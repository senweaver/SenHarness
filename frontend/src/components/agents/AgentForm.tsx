"use client";

import { useEffect, useState } from "react";
import { useRouter } from "@/lib/navigation";
import { useTranslations } from "next-intl";
import { toast } from "sonner";
import { IconLoader2, IconTrash } from "@tabler/icons-react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { Switch } from "@/components/ui/switch";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import type { AgentRead } from "@/types/api";
import {
  useCreateAgent,
  useDeleteAgent,
  useUpdateAgent,
  type AgentCreateInput,
} from "@/hooks/use-agent-mutations";
import { useBackendAdapters } from "@/hooks/use-backend-adapters";

interface AgentFormProps {
  mode: "create" | "edit";
  initial?: AgentRead;
}

/**
 * Full set of tools that can be gated behind a human approval. Kept in sync
 * with ``backend/app/agents/harness/approvals.py::DEFAULT_APPROVAL_TOOLS``
 * plus the common network-reach tools (``web_fetch``). Users who need to gate
 * a custom tool can flip the agent to ``specific`` and pass its name.
 */
const APPROVAL_TOOL_OPTIONS: string[] = [
  "execute",
  "write_file",
  "edit_file",
  "delete_file",
  "read_file",
  "web_fetch",
];

const DEFAULT_APPROVAL_TOOLS: string[] = [
  "execute",
  "write_file",
  "edit_file",
  "delete_file",
];

export function AgentForm({ mode, initial }: AgentFormProps) {
  const t = useTranslations("settings.agents.form");
  const tAgents = useTranslations("settings.agents");
  const tCommon = useTranslations("common");
  const tSettings = useTranslations("settings");
  const router = useRouter();

  const [name, setName] = useState(initial?.name ?? "");
  const [description, setDescription] = useState(initial?.description ?? "");
  const [avatarUrl, setAvatarUrl] = useState(initial?.avatar_url ?? "");
  const [persona, setPersona] = useState(initial?.persona_md ?? "");
  const [backendKind, setBackendKind] = useState<"native" | "openclaw">(
    initial?.backend_kind ?? "native",
  );
  const [backendAdapterId, setBackendAdapterId] = useState<string | null>(
    initial?.backend_adapter_id ?? null,
  );
  const adaptersQ = useBackendAdapters();
  const adapters = adaptersQ.data ?? [];
  const [autonomy, setAutonomy] = useState<"l1" | "l2" | "l3">(initial?.autonomy_level ?? "l2");
  const [visibility, setVisibility] = useState<"private" | "workspace" | "public">(
    initial?.visibility ?? "workspace",
  );
  const [codeMode, setCodeMode] = useState<boolean>(
    Boolean((initial?.metadata_json as Record<string, unknown> | undefined)?.code_mode),
  );

  const initialSandbox = ((initial?.metadata_json as Record<string, unknown> | undefined)
    ?.sandbox ?? null) as string | boolean | Record<string, unknown> | null;
  const [sandboxKind, setSandboxKind] = useState<"off" | "local" | "docker">(() => {
    if (initialSandbox === null || initialSandbox === false) return "off";
    if (initialSandbox === true || initialSandbox === "local") return "local";
    if (typeof initialSandbox === "string") return (initialSandbox as "docker");
    if (typeof initialSandbox === "object" && initialSandbox !== null) {
      const k = (initialSandbox as Record<string, unknown>).kind;
      if (k === "docker") return "docker";
      if (k === "state") return "off";
      return "local";
    }
    return "off";
  });
  const initialApprovals = (initial?.metadata_json as Record<string, unknown> | undefined)
    ?.approvals;
  const [approvalMode, setApprovalMode] = useState<"off" | "all" | "specific">(() => {
    if (Array.isArray(initialApprovals)) return "specific";
    if (initialApprovals === true) return "all";
    return "off";
  });
  const [approvalTools, setApprovalTools] = useState<string[]>(() =>
    Array.isArray(initialApprovals)
      ? (initialApprovals as unknown[]).map((x) => String(x))
      : DEFAULT_APPROVAL_TOOLS,
  );
  const [approvalTtl, setApprovalTtl] = useState<number>(() => {
    const raw = (initial?.metadata_json as Record<string, unknown> | undefined)
      ?.approval_ttl_seconds;
    const n = typeof raw === "number" ? raw : Number(raw ?? 300);
    return Number.isFinite(n) && n > 0 ? n : 300;
  });

  // ─── Shields & Budget (C1) ──────────────────────────────
  // ``shields`` lives at metadata_json.shields with shape:
  //   {pii: "log"|"block"|false, injection: "low"|"medium"|"high"|false,
  //    secrets: bool, blocked_keywords: string[]}
  // We map that to a small set of UI toggles + a comma-separated keyword
  // input. Boolean OFF becomes false in the JSON so the runtime skips it.
  const initialShields = ((initial?.metadata_json as Record<string, unknown> | undefined)
    ?.shields ?? null) as
    | null
    | boolean
    | {
        pii?: string | boolean;
        injection?: string | boolean;
        secrets?: boolean;
        blocked_keywords?: string[];
      };
  const [shieldsEnabled, setShieldsEnabled] = useState<boolean>(
    initialShields !== null && initialShields !== false,
  );
  const initialPii = (() => {
    if (typeof initialShields === "object" && initialShields) {
      const p = initialShields.pii;
      if (p === "block" || p === "log") return p;
    }
    return "log" as "log" | "block" | "off";
  })();
  const [piiMode, setPiiMode] = useState<"off" | "log" | "block">(initialPii);
  const initialInjection = (() => {
    if (typeof initialShields === "object" && initialShields) {
      const v = initialShields.injection;
      if (v === "low" || v === "medium" || v === "high") return v;
    }
    return "off" as "off" | "low" | "medium" | "high";
  })();
  const [injection, setInjection] = useState<"off" | "low" | "medium" | "high">(
    initialInjection,
  );
  const [secretsRedact, setSecretsRedact] = useState<boolean>(
    typeof initialShields === "object" && initialShields
      ? Boolean(initialShields.secrets)
      : false,
  );
  const [blockedKeywords, setBlockedKeywords] = useState<string>(
    typeof initialShields === "object" &&
      initialShields &&
      Array.isArray(initialShields.blocked_keywords)
      ? initialShields.blocked_keywords.join(", ")
      : "",
  );

  // ``budget`` lives at metadata_json.budget = {usd: number, on_exceed: "warn"|"stop"}.
  const initialBudget = ((initial?.metadata_json as Record<string, unknown> | undefined)
    ?.budget ?? null) as
    | null
    | number
    | { usd?: number; limit?: number; on_exceed?: string };
  const [budgetEnabled, setBudgetEnabled] = useState<boolean>(initialBudget !== null);
  const [budgetUsd, setBudgetUsd] = useState<number>(() => {
    if (typeof initialBudget === "number") return initialBudget;
    if (typeof initialBudget === "object" && initialBudget) {
      return Number(initialBudget.usd ?? initialBudget.limit ?? 1.0) || 1.0;
    }
    return 1.0;
  });
  const [budgetOnExceed, setBudgetOnExceed] = useState<"warn" | "stop">(() => {
    if (typeof initialBudget === "object" && initialBudget) {
      return initialBudget.on_exceed === "warn" ? "warn" : "stop";
    }
    return "stop";
  });

  const create = useCreateAgent();
  // Hooks must run unconditionally (rules-of-hooks). The "" sentinel is
  // safe — ``update.mutateAsync`` is only invoked when ``initial`` is set
  // (see submit() below).
  const update = useUpdateAgent(initial?.id ?? "");
  const remove = useDeleteAgent();

  useEffect(() => {
    if (!initial) return;
    setName(initial.name ?? "");
    setDescription(initial.description ?? "");
    setAvatarUrl(initial.avatar_url ?? "");
    setPersona(initial.persona_md ?? "");
    setBackendKind(initial.backend_kind);
    setBackendAdapterId(initial.backend_adapter_id ?? null);
    setAutonomy(initial.autonomy_level);
    setVisibility(initial.visibility);
    setCodeMode(Boolean((initial.metadata_json as Record<string, unknown>)?.code_mode));
    const sb = (initial.metadata_json as Record<string, unknown>)?.sandbox;
    if (sb === null || sb === undefined || sb === false) setSandboxKind("off");
    else if (sb === true || sb === "local") setSandboxKind("local");
    else if (sb === "docker") setSandboxKind("docker");
    else if (typeof sb === "object") {
      const k = (sb as Record<string, unknown>).kind;
      setSandboxKind(k === "docker" ? "docker" : "local");
    }
    const appr = (initial.metadata_json as Record<string, unknown>)?.approvals;
    if (Array.isArray(appr)) {
      setApprovalMode("specific");
      setApprovalTools(appr.map((x) => String(x)));
    } else if (appr === true) {
      setApprovalMode("all");
    } else {
      setApprovalMode("off");
    }
    const ttl = (initial.metadata_json as Record<string, unknown>)?.approval_ttl_seconds;
    const ttlN = typeof ttl === "number" ? ttl : Number(ttl ?? 300);
    setApprovalTtl(Number.isFinite(ttlN) && ttlN > 0 ? ttlN : 300);

    // shields
    const sh = (initial.metadata_json as Record<string, unknown>)?.shields as
      | null
      | boolean
      | { pii?: string; injection?: string; secrets?: boolean; blocked_keywords?: string[] };
    setShieldsEnabled(sh !== null && sh !== false && sh !== undefined);
    if (typeof sh === "object" && sh) {
      setPiiMode(sh.pii === "block" ? "block" : sh.pii === "log" ? "log" : "log");
      const inj = sh.injection;
      setInjection(
        inj === "low" || inj === "medium" || inj === "high" ? inj : "off",
      );
      setSecretsRedact(Boolean(sh.secrets));
      setBlockedKeywords(
        Array.isArray(sh.blocked_keywords) ? sh.blocked_keywords.join(", ") : "",
      );
    }

    // budget
    const bd = (initial.metadata_json as Record<string, unknown>)?.budget as
      | null
      | number
      | { usd?: number; limit?: number; on_exceed?: string };
    setBudgetEnabled(bd !== null && bd !== undefined);
    if (typeof bd === "number") {
      setBudgetUsd(bd);
      setBudgetOnExceed("stop");
    } else if (typeof bd === "object" && bd) {
      setBudgetUsd(Number(bd.usd ?? bd.limit ?? 1.0) || 1.0);
      setBudgetOnExceed(bd.on_exceed === "warn" ? "warn" : "stop");
    }
  }, [initial?.id]);

  const submit = async () => {
    const payload: AgentCreateInput = {
      name,
      description: description || null,
      avatar_url: avatarUrl || null,
      persona_md: persona || null,
      backend_kind: backendKind,
      backend_adapter_id:
        backendKind === "openclaw" ? backendAdapterId : null,
      autonomy_level: autonomy,
      visibility,
      metadata_json: {
        ...(initial?.metadata_json ?? {}),
        code_mode: codeMode ? "all" : false,
        sandbox:
          sandboxKind === "off"
            ? false
            : sandboxKind === "docker"
              ? { kind: "docker", image: "python:3.12-slim" }
              : "local",
        approvals:
          approvalMode === "off"
            ? false
            : approvalMode === "all"
              ? true
              : approvalTools,
        approval_ttl_seconds: Math.max(60, Math.min(3600, Math.round(approvalTtl))),
        shields: shieldsEnabled
          ? {
              pii: piiMode === "off" ? false : piiMode,
              injection: injection === "off" ? false : injection,
              secrets: secretsRedact,
              blocked_keywords: blockedKeywords
                .split(",")
                .map((k) => k.trim())
                .filter(Boolean),
            }
          : false,
        budget: budgetEnabled
          ? {
              usd: Math.max(0.01, Number(budgetUsd) || 1.0),
              on_exceed: budgetOnExceed,
            }
          : null,
      },
    };

    try {
      if (mode === "edit" && initial) {
        await update.mutateAsync(payload);
        toast.success(tSettings("saved"));
      } else {
        const created = await create.mutateAsync(payload);
        toast.success(tSettings("created"));
        router.push(`/agents/${created.id}/edit`);
      }
    } catch {
      toast.error(tSettings(mode === "edit" ? "saveFailed" : "createFailed"));
    }
  };

  const onDelete = async () => {
    if (!initial) return;
    if (!confirm(tAgents("confirmDelete"))) return;
    try {
      await remove.mutateAsync(initial.id);
      toast.success(tSettings("deleted"));
      router.push("/agents");
    } catch {
      toast.error(tSettings("deleteFailed"));
    }
  };

  const submitting = create.isPending || update.isPending || remove.isPending;

  return (
    <div className="space-y-4">
      <Card>
        <CardHeader>
          <CardTitle>{mode === "create" ? tAgents("new") : tAgents("edit")}</CardTitle>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="grid gap-3 sm:grid-cols-2">
            <Field id="name" label={t("name")}>
              <Input
                id="name"
                value={name}
                onChange={(e) => setName(e.target.value)}
                required
                data-testid="agent-form-name"
              />
            </Field>
            <Field id="avatar" label={t("avatarUrl")}>
              <Input id="avatar" value={avatarUrl} onChange={(e) => setAvatarUrl(e.target.value)} placeholder="https://…" />
            </Field>
          </div>

          <Field id="description" label={t("description")}>
            <Input
              id="description"
              value={description}
              onChange={(e) => setDescription(e.target.value)}
              placeholder={t("descriptionPlaceholder")}
              data-testid="agent-form-description"
            />
          </Field>

          <Field id="persona" label={t("persona")} description={t("personaHint")}>
            <Textarea
              id="persona"
              value={persona}
              onChange={(e) => setPersona(e.target.value)}
              className="min-h-[180px] font-mono text-[13px]"
              placeholder={t("personaPlaceholder")}
              data-testid="agent-form-persona"
            />
          </Field>

          <div className="grid gap-3 sm:grid-cols-3">
            <Field id="backend" label={t("backendKind")}>
              <Select value={backendKind} onValueChange={(v) => setBackendKind(v as "native")}>
                <SelectTrigger>
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="native">NativeRuntime</SelectItem>
                  <SelectItem value="openclaw">OpenClaw (remote)</SelectItem>
                </SelectContent>
              </Select>
            </Field>
            {backendKind === "openclaw" && (
              <Field
                id="backend-adapter"
                label={t("backendAdapter")}
                description={t("backendAdapterHint")}
              >
                <Select
                  value={backendAdapterId ?? ""}
                  onValueChange={(v) =>
                    setBackendAdapterId(v && v !== "__none__" ? v : null)
                  }
                >
                  <SelectTrigger id="backend-adapter">
                    <SelectValue placeholder={t("backendAdapterPlaceholder")} />
                  </SelectTrigger>
                  <SelectContent>
                    {adapters.length === 0 ? (
                      <SelectItem value="__none__" disabled>
                        {t("backendAdapterEmpty")}
                      </SelectItem>
                    ) : (
                      adapters.map((a) => (
                        <SelectItem key={a.id} value={a.id}>
                          {a.name}
                          {a.health_status === "down"
                            ? " · down"
                            : a.health_status === "degraded"
                              ? " · degraded"
                              : ""}
                        </SelectItem>
                      ))
                    )}
                  </SelectContent>
                </Select>
              </Field>
            )}
            <Field id="autonomy" label={t("autonomy")}>
              <Select value={autonomy} onValueChange={(v) => setAutonomy(v as "l2")}>
                <SelectTrigger>
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="l1">{t("autonomyL1")}</SelectItem>
                  <SelectItem value="l2">{t("autonomyL2")}</SelectItem>
                  <SelectItem value="l3">{t("autonomyL3")}</SelectItem>
                </SelectContent>
              </Select>
            </Field>
            <Field id="visibility" label={t("visibility")}>
              <Select value={visibility} onValueChange={(v) => setVisibility(v as "workspace")}>
                <SelectTrigger>
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="private">{t("visibilityPrivate")}</SelectItem>
                  <SelectItem value="workspace">{t("visibilityWorkspace")}</SelectItem>
                  <SelectItem value="public">{t("visibilityPublic")}</SelectItem>
                </SelectContent>
              </Select>
            </Field>
          </div>

          <div className="flex items-center justify-between rounded-md border p-3">
            <div>
              <div className="text-sm font-medium">{t("codeMode")}</div>
              <div className="mt-0.5 text-[11px] sh-muted">{t("codeModeHint")}</div>
            </div>
            <Switch checked={codeMode} onCheckedChange={setCodeMode} />
          </div>

          <div className="rounded-md border p-3">
            <div className="mb-2 flex items-center justify-between gap-3">
              <div>
                <div className="text-sm font-medium">{t("sandboxTitle")}</div>
                <div className="mt-0.5 text-[11px] sh-muted">
                  {t("sandboxHint")}
                </div>
              </div>
              <Select value={sandboxKind} onValueChange={(v) => setSandboxKind(v as "off")}>
                <SelectTrigger className="w-[140px]">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="off">{t("sandboxOff")}</SelectItem>
                  <SelectItem value="local">{t("sandboxLocal")}</SelectItem>
                  <SelectItem value="docker">{t("sandboxDocker")}</SelectItem>
                </SelectContent>
              </Select>
            </div>
          </div>

          <div className="rounded-md border p-3">
            <div className="mb-2 flex items-start justify-between gap-3">
              <div className="min-w-0">
                <div className="text-sm font-medium">{t("approvalsTitle")}</div>
                <div className="mt-0.5 text-[11px] sh-muted">
                  {t("approvalsHint")}
                </div>
              </div>
              <Select
                value={approvalMode}
                onValueChange={(v) => setApprovalMode(v as "off" | "all" | "specific")}
              >
                <SelectTrigger className="w-[180px]">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="off">{t("approvalModeOff")}</SelectItem>
                  <SelectItem value="all">{t("approvalModeAll")}</SelectItem>
                  <SelectItem value="specific">{t("approvalModeSpecific")}</SelectItem>
                </SelectContent>
              </Select>
            </div>

            {approvalMode === "specific" && (
              <div className="mt-2 grid gap-2 rounded bg-black/5 p-2 sm:grid-cols-3 dark:bg-white/5">
                {APPROVAL_TOOL_OPTIONS.map((tool) => {
                  const checked = approvalTools.includes(tool);
                  return (
                    <label
                      key={tool}
                      className="flex items-center gap-2 text-[12px]"
                    >
                      <input
                        type="checkbox"
                        checked={checked}
                        onChange={(e) => {
                          setApprovalTools((prev) =>
                            e.target.checked
                              ? Array.from(new Set([...prev, tool]))
                              : prev.filter((t) => t !== tool),
                          );
                        }}
                      />
                      <span className="font-mono text-[11px]">{tool}</span>
                    </label>
                  );
                })}
              </div>
            )}

            {approvalMode !== "off" && (
              <div className="mt-3 flex items-center gap-2 text-[12px]">
                <Label htmlFor="approval-ttl" className="shrink-0">
                  {t("approvalTtl")}
                </Label>
                <Input
                  id="approval-ttl"
                  type="number"
                  min={60}
                  max={3600}
                  step={30}
                  className="w-[120px]"
                  value={approvalTtl}
                  onChange={(e) => {
                    const n = Number(e.target.value);
                    setApprovalTtl(Number.isFinite(n) ? n : 300);
                  }}
                />
                <span className="text-[11px] sh-muted">
                  {t("approvalTtlHint")}
                </span>
              </div>
            )}
          </div>

          <div className="rounded-md border p-3">
            <div className="mb-2 flex items-start justify-between gap-3">
              <div className="min-w-0">
                <div className="text-sm font-medium">{t("shieldsTitle")}</div>
                <div className="mt-0.5 text-[11px] sh-muted">
                  {t("shieldsHint")}
                </div>
              </div>
              <Switch
                checked={shieldsEnabled}
                onCheckedChange={setShieldsEnabled}
              />
            </div>
            {shieldsEnabled && (
              <div className="mt-2 grid gap-3 sm:grid-cols-2">
                <Field id="pii-mode" label={t("piiLabel")}>
                  <Select
                    value={piiMode}
                    onValueChange={(v) =>
                      setPiiMode(v as "off" | "log" | "block")
                    }
                  >
                    <SelectTrigger id="pii-mode">
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                      <SelectItem value="off">{t("piiOff")}</SelectItem>
                      <SelectItem value="log">{t("piiLog")}</SelectItem>
                      <SelectItem value="block">{t("piiBlock")}</SelectItem>
                    </SelectContent>
                  </Select>
                </Field>
                <Field id="injection" label={t("injectionLabel")}>
                  <Select
                    value={injection}
                    onValueChange={(v) =>
                      setInjection(v as "off" | "low" | "medium" | "high")
                    }
                  >
                    <SelectTrigger id="injection">
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                      <SelectItem value="off">{t("injectionOff")}</SelectItem>
                      <SelectItem value="low">{t("injectionLow")}</SelectItem>
                      <SelectItem value="medium">{t("injectionMedium")}</SelectItem>
                      <SelectItem value="high">{t("injectionHigh")}</SelectItem>
                    </SelectContent>
                  </Select>
                </Field>
                <div className="flex items-center justify-between rounded-md border p-2 sm:col-span-2">
                  <div className="text-[12px]">
                    <div className="font-medium">{t("secretsTitle")}</div>
                    <div className="mt-0.5 text-[11px] sh-muted">
                      {t("secretsHint")}
                    </div>
                  </div>
                  <Switch
                    checked={secretsRedact}
                    onCheckedChange={setSecretsRedact}
                  />
                </div>
                <Field
                  id="blocked-kw"
                  label={t("blockedKeywordsLabel")}
                  description={t("blockedKeywordsHint")}
                >
                  <Input
                    id="blocked-kw"
                    value={blockedKeywords}
                    onChange={(e) => setBlockedKeywords(e.target.value)}
                    placeholder="ssn, internal-only, …"
                  />
                </Field>
              </div>
            )}
          </div>

          <div className="rounded-md border p-3">
            <div className="mb-2 flex items-start justify-between gap-3">
              <div className="min-w-0">
                <div className="text-sm font-medium">{t("budgetTitle")}</div>
                <div className="mt-0.5 text-[11px] sh-muted">
                  {t("budgetHint")}
                </div>
              </div>
              <Switch
                checked={budgetEnabled}
                onCheckedChange={setBudgetEnabled}
              />
            </div>
            {budgetEnabled && (
              <div className="mt-2 grid gap-3 sm:grid-cols-2">
                <Field id="budget-usd" label={t("budgetUsdLabel")}>
                  <Input
                    id="budget-usd"
                    type="number"
                    min={0.01}
                    step={0.1}
                    value={budgetUsd}
                    onChange={(e) => {
                      const n = Number(e.target.value);
                      setBudgetUsd(Number.isFinite(n) && n > 0 ? n : 1.0);
                    }}
                  />
                </Field>
                <Field id="budget-on-exceed" label={t("budgetOnExceedLabel")}>
                  <Select
                    value={budgetOnExceed}
                    onValueChange={(v) =>
                      setBudgetOnExceed(v as "warn" | "stop")
                    }
                  >
                    <SelectTrigger id="budget-on-exceed">
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                      <SelectItem value="warn">{t("budgetWarn")}</SelectItem>
                      <SelectItem value="stop">{t("budgetStop")}</SelectItem>
                    </SelectContent>
                  </Select>
                </Field>
              </div>
            )}
          </div>
        </CardContent>
      </Card>

      <div className="flex items-center justify-between">
        {mode === "edit" && initial ? (
          <Button variant="destructive" onClick={onDelete} disabled={submitting}>
            <IconTrash className="size-4" />
            {tCommon("delete")}
          </Button>
        ) : (
          <span />
        )}
        <div className="flex gap-2">
          <Button variant="ghost" onClick={() => router.back()} disabled={submitting}>
            {t("cancel")}
          </Button>
          <Button
            onClick={submit}
            disabled={submitting || !name.trim()}
            data-testid="agent-form-submit"
          >
            {submitting && <IconLoader2 className="size-4 animate-spin" />}
            {t("save")}
          </Button>
        </div>
      </div>
    </div>
  );
}

function Field({
  id,
  label,
  description,
  children,
}: {
  id: string;
  label: string;
  description?: string;
  children: React.ReactNode;
}) {
  return (
    <div className="grid gap-1.5">
      <Label htmlFor={id}>{label}</Label>
      {children}
      {description && <p className="text-[11px] sh-muted">{description}</p>}
    </div>
  );
}
