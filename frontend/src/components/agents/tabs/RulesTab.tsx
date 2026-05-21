"use client";

import { useEffect, useState } from "react";
import { IconCheck, IconLoader2 } from "@tabler/icons-react";
import { useTranslations } from "next-intl";
import { toast } from "sonner";

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
import { useUpdateAgent } from "@/hooks/use-agent-mutations";
import type { AgentRead } from "@/types/api";

interface RulesTabProps {
  agent: AgentRead;
}

type ApprovalMode = "off" | "all" | "specific";

const APPROVAL_TTL_MIN = 60;
const APPROVAL_TTL_MAX = 3600;
const APPROVAL_TTL_DEFAULT = 300;

/**
 * RulesTab — inline-edit the four governance dimensions of an agent
 * (visibility / approvals / shields / budget). Each control auto-
 * commits via `useUpdateAgent` on change/blur so there's no Save
 * button to forget about (plan §3 RulesTab spec: "inline + blur
 * autosave"). Backend already validates and audits the patch.
 */
export function RulesTab({ agent }: RulesTabProps) {
  const t = useTranslations("agentDetail.rules");
  const tForm = useTranslations("settings.agents.form");
  const update = useUpdateAgent(agent.id);

  const meta = (agent.metadata_json ?? {}) as Record<string, unknown>;
  const initialApprovalMode = readApprovalMode(meta);
  const initialBudget = readBudget(meta);
  const initialPii = readShield(meta, "pii", "off");
  const initialInjection = readShield(meta, "injection", "off");
  const initialSecrets = Boolean(
    (meta.shields as Record<string, unknown> | undefined)?.secrets ?? false,
  );

  const [approvalMode, setApprovalMode] = useState<ApprovalMode>(
    initialApprovalMode,
  );
  const [approvalTtl, setApprovalTtl] = useState<string>(
    String(readApprovalTtl(meta)),
  );
  const [pii, setPii] = useState<string>(initialPii);
  const [injection, setInjection] = useState<string>(initialInjection);
  const [secrets, setSecrets] = useState<boolean>(initialSecrets);
  const [budgetUsd, setBudgetUsd] = useState<string>(
    initialBudget.usd != null ? String(initialBudget.usd) : "",
  );
  const [budgetOnExceed, setBudgetOnExceed] = useState<string>(
    initialBudget.on_exceed ?? "warn",
  );
  const [savingField, setSavingField] = useState<string | null>(null);
  const [savedField, setSavedField] = useState<string | null>(null);

  // Re-seed when the parent reloads the agent (after a successful save
  // the cache flips and a fresh `agent` arrives).
  useEffect(() => {
    const m = (agent.metadata_json ?? {}) as Record<string, unknown>;
    setApprovalMode(readApprovalMode(m));
    setApprovalTtl(String(readApprovalTtl(m)));
    setPii(readShield(m, "pii", "off"));
    setInjection(readShield(m, "injection", "off"));
    setSecrets(
      Boolean((m.shields as Record<string, unknown> | undefined)?.secrets),
    );
    const b = readBudget(m);
    setBudgetUsd(b.usd != null ? String(b.usd) : "");
    setBudgetOnExceed(b.on_exceed ?? "warn");
  }, [agent.id, agent.metadata_json]);

  const flash = (field: string) => {
    setSavedField(field);
    window.setTimeout(
      () => setSavedField((cur) => (cur === field ? null : cur)),
      1500,
    );
  };

  const commit = async (
    field: string,
    body: { metadata_json?: Record<string, unknown> },
  ) => {
    setSavingField(field);
    try {
      await update.mutateAsync(body);
      flash(field);
    } catch {
      toast.error("Save failed");
    } finally {
      setSavingField(null);
    }
  };

  const patchMeta = (updater: (m: Record<string, unknown>) => Record<string, unknown>) => {
    return updater({ ...(agent.metadata_json ?? {}) });
  };

  const onApprovalModeChange = (mode: ApprovalMode) => {
    setApprovalMode(mode);
    const meta = patchMeta((m) => {
      if (mode === "off") {
        m.approvals = false;
      } else if (mode === "all") {
        m.approvals = { mode: "all" };
      } else {
        m.approvals = { mode: "specific", tools: [] };
      }
      return m;
    });
    void commit("approvals", { metadata_json: meta });
  };

  const onApprovalTtlCommit = () => {
    const raw = approvalTtl.trim();
    const n = raw === "" ? APPROVAL_TTL_DEFAULT : Number(raw);
    if (!Number.isFinite(n)) {
      setApprovalTtl(String(readApprovalTtl(agent.metadata_json ?? {})));
      return;
    }
    const clamped = Math.max(
      APPROVAL_TTL_MIN,
      Math.min(APPROVAL_TTL_MAX, Math.round(n)),
    );
    setApprovalTtl(String(clamped));
    const meta = patchMeta((m) => {
      m.approval_ttl_seconds = clamped;
      return m;
    });
    void commit("approval_ttl", { metadata_json: meta });
  };

  const onShieldChange = (key: "pii" | "injection", v: string) => {
    if (key === "pii") setPii(v);
    else setInjection(v);
    const meta = patchMeta((m) => {
      const shields = (m.shields as Record<string, unknown>) ?? {};
      shields[key] = v;
      m.shields = shields;
      return m;
    });
    void commit(`shield_${key}`, { metadata_json: meta });
  };

  const onSecretsToggle = (v: boolean) => {
    setSecrets(v);
    const meta = patchMeta((m) => {
      const shields = (m.shields as Record<string, unknown>) ?? {};
      shields.secrets = v;
      m.shields = shields;
      return m;
    });
    void commit("shield_secrets", { metadata_json: meta });
  };

  const onBudgetCommit = () => {
    const usd = budgetUsd.trim() ? Number(budgetUsd) : null;
    if (usd !== null && (Number.isNaN(usd) || usd < 0)) {
      toast.error(tForm("budgetUsdLabel") + " must be a positive number");
      setBudgetUsd(initialBudget.usd != null ? String(initialBudget.usd) : "");
      return;
    }
    const meta = patchMeta((m) => {
      if (usd === null) {
        delete m.budget;
      } else {
        m.budget = { usd, on_exceed: budgetOnExceed };
      }
      return m;
    });
    void commit("budget", { metadata_json: meta });
  };

  return (
    <div className="space-y-4">
      <header>
        <h2 className="text-base font-semibold">{t("title")}</h2>
      </header>

      <Section
        title={t("approvals")}
        savingField={savingField}
        savedField={savedField}
        fieldKey="approvals"
      >
        <div className="grid gap-3 sm:grid-cols-2">
          <div className="space-y-1.5">
            <Label className="text-[12px]">{tForm("approvalModeLabel")}</Label>
            <Select
              value={approvalMode}
              onValueChange={(v) => onApprovalModeChange(v as ApprovalMode)}
            >
              <SelectTrigger>
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="off">{tForm("approvalModeOff")}</SelectItem>
                <SelectItem value="all">{tForm("approvalModeAll")}</SelectItem>
                <SelectItem value="specific">
                  {tForm("approvalModeSpecific")}
                </SelectItem>
              </SelectContent>
            </Select>
          </div>
          <div className="space-y-1.5">
            <Label className="text-[12px]">{tForm("approvalTtlLabel")}</Label>
            <Input
              type="number"
              min={APPROVAL_TTL_MIN}
              max={APPROVAL_TTL_MAX}
              step="30"
              value={approvalTtl}
              onChange={(e) => setApprovalTtl(e.target.value)}
              onBlur={onApprovalTtlCommit}
              disabled={approvalMode === "off"}
            />
          </div>
        </div>
        <p className="mt-1.5 text-[11px] sh-muted">{tForm("approvalsHint")}</p>
      </Section>

      <Section
        title={t("shields")}
        savingField={savingField}
        savedField={savedField}
        fieldKey="shield"
      >
        <div className="grid gap-3 sm:grid-cols-2">
          <div className="space-y-1.5">
            <Label className="text-[12px]">{tForm("piiLabel")}</Label>
            <Select
              value={pii}
              onValueChange={(v) => onShieldChange("pii", v)}
            >
              <SelectTrigger>
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="off">{tForm("piiOff")}</SelectItem>
                <SelectItem value="log">{tForm("piiLog")}</SelectItem>
                <SelectItem value="block">{tForm("piiBlock")}</SelectItem>
              </SelectContent>
            </Select>
          </div>
          <div className="space-y-1.5">
            <Label className="text-[12px]">{tForm("injectionLabel")}</Label>
            <Select
              value={injection}
              onValueChange={(v) => onShieldChange("injection", v)}
            >
              <SelectTrigger>
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="off">{tForm("injectionOff")}</SelectItem>
                <SelectItem value="low">{tForm("injectionLow")}</SelectItem>
                <SelectItem value="medium">
                  {tForm("injectionMedium")}
                </SelectItem>
                <SelectItem value="high">{tForm("injectionHigh")}</SelectItem>
              </SelectContent>
            </Select>
          </div>
        </div>
        <div className="mt-3 flex items-center gap-2">
          <Switch
            id="secrets-toggle"
            checked={secrets}
            onCheckedChange={onSecretsToggle}
          />
          <Label htmlFor="secrets-toggle" className="cursor-pointer text-[12px]">
            {tForm("secretsTitle")}
          </Label>
        </div>
      </Section>

      <Section
        title={t("budget")}
        savingField={savingField}
        savedField={savedField}
        fieldKey="budget"
      >
        <div className="grid gap-3 sm:grid-cols-2">
          <div className="space-y-1.5">
            <Label className="text-[12px]">{tForm("budgetUsdLabel")}</Label>
            <Input
              type="number"
              min="0"
              step="0.5"
              value={budgetUsd}
              onChange={(e) => setBudgetUsd(e.target.value)}
              onBlur={onBudgetCommit}
              placeholder="∞"
            />
          </div>
          <div className="space-y-1.5">
            <Label className="text-[12px]">{tForm("budgetOnExceedLabel")}</Label>
            <Select
              value={budgetOnExceed}
              onValueChange={(v) => {
                setBudgetOnExceed(v);
                window.setTimeout(onBudgetCommit, 0);
              }}
            >
              <SelectTrigger>
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="warn">{tForm("budgetWarn")}</SelectItem>
                <SelectItem value="stop">{tForm("budgetStop")}</SelectItem>
              </SelectContent>
            </Select>
          </div>
        </div>
        <p className="mt-1.5 text-[11px] sh-muted">{tForm("budgetHint")}</p>
      </Section>
    </div>
  );
}

function Section({
  title,
  savingField,
  savedField,
  fieldKey,
  children,
}: {
  title: string;
  savingField: string | null;
  savedField: string | null;
  fieldKey: string;
  children: React.ReactNode;
}) {
  const isSaving = savingField?.startsWith(fieldKey);
  const isSaved = savedField?.startsWith(fieldKey);
  return (
    <div className="rounded-md border sh-card p-4">
      <div className="mb-2 flex items-center justify-between">
        <div className="text-[11px] font-semibold uppercase tracking-wide sh-muted">
          {title}
        </div>
        {isSaving ? (
          <span className="inline-flex items-center gap-1 text-[11px] sh-muted">
            <IconLoader2 className="size-3 animate-spin" />
            Saving…
          </span>
        ) : isSaved ? (
          <span className="inline-flex items-center gap-1 text-[11px] text-emerald-600">
            <IconCheck className="size-3" />
            Saved
          </span>
        ) : null}
      </div>
      {children}
    </div>
  );
}

function readApprovalMode(meta: Record<string, unknown>): ApprovalMode {
  const a = meta.approvals;
  if (a === false || a == null) return "off";
  if (a === true) return "all";
  if (typeof a === "object") {
    const mode = (a as Record<string, unknown>).mode;
    if (mode === "all") return "all";
    if (mode === "specific") return "specific";
  }
  return "off";
}

function readApprovalTtl(meta: Record<string, unknown>): number {
  const raw = meta.approval_ttl_seconds;
  const n = typeof raw === "number" ? raw : Number(raw ?? APPROVAL_TTL_DEFAULT);
  if (!Number.isFinite(n) || n <= 0) return APPROVAL_TTL_DEFAULT;
  return Math.max(APPROVAL_TTL_MIN, Math.min(APPROVAL_TTL_MAX, Math.round(n)));
}

function readShield(
  meta: Record<string, unknown>,
  key: string,
  fallback: string,
): string {
  const shields = (meta.shields as Record<string, unknown> | undefined) ?? {};
  const v = shields[key];
  return typeof v === "string" ? v : fallback;
}

function readBudget(meta: Record<string, unknown>): {
  usd: number | null;
  on_exceed: string | null;
} {
  const b = meta.budget as Record<string, unknown> | undefined;
  if (!b) return { usd: null, on_exceed: null };
  const usd = typeof b.usd === "number" ? b.usd : null;
  const on_exceed = typeof b.on_exceed === "string" ? b.on_exceed : null;
  return { usd, on_exceed };
}
