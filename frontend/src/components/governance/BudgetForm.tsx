"use client";

/**
 * BudgetForm — Dialog with create + edit modes for governance budgets.
 *
 * Matches `app/schemas/governance.py::BudgetCreate` — period is one of
 * daily/weekly/monthly (matching `BudgetPeriod` in the DB model). Currency
 * is a free string; the bundled choices cover the usual enterprise cases
 * but any ISO-4217 code is accepted by the server.
 */

import { useEffect, useState } from "react";
import { IconLoader2 } from "@tabler/icons-react";
import { useTranslations } from "next-intl";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
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
import { useAgents } from "@/hooks/use-agents";
import {
  type BudgetPeriod,
  type BudgetRead,
  type GovernanceScope,
  useCreateBudget,
  useUpdateBudget,
} from "@/hooks/use-governance";

interface BudgetFormProps {
  mode: "create" | "edit";
  open: boolean;
  onOpenChange: (open: boolean) => void;
  initial?: BudgetRead;
  allowedScopes: GovernanceScope[];
  forceScope?: GovernanceScope;
}

const CURRENCIES = ["USD", "EUR", "CNY", "JPY", "KRW", "GBP", "HKD", "SGD", "AUD", "CAD"];
const PERIODS: BudgetPeriod[] = ["daily", "weekly", "monthly"];

export function BudgetForm({
  mode,
  open,
  onOpenChange,
  initial,
  allowedScopes,
  forceScope,
}: BudgetFormProps) {
  const t = useTranslations("settings.governance");
  const tCommon = useTranslations("common");
  const create = useCreateBudget();
  const update = useUpdateBudget(initial?.id ?? "");
  const agentsQ = useAgents();
  const agents = agentsQ.data ?? [];

  const [name, setName] = useState(initial?.name ?? "");
  const [scope, setScope] = useState<GovernanceScope>(
    forceScope ?? initial?.scope ?? allowedScopes[0] ?? "workspace",
  );
  const [agentId, setAgentId] = useState<string>(initial?.agent_id ?? "");
  const [currency, setCurrency] = useState<string>(initial?.currency ?? "USD");
  const [period, setPeriod] = useState<BudgetPeriod>(initial?.period ?? "monthly");
  const [limitAmount, setLimitAmount] = useState<string>(
    initial?.limit_amount ?? "10",
  );
  const [alertPct, setAlertPct] = useState<number | "">(
    initial?.alert_threshold_pct ?? 80,
  );
  const [enabled, setEnabled] = useState<boolean>(initial?.enabled ?? true);

  useEffect(() => {
    if (!open) return;
    setName(initial?.name ?? "");
    setScope(forceScope ?? initial?.scope ?? allowedScopes[0] ?? "workspace");
    setAgentId(initial?.agent_id ?? "");
    setCurrency(initial?.currency ?? "USD");
    setPeriod(initial?.period ?? "monthly");
    setLimitAmount(initial?.limit_amount ?? "10");
    setAlertPct(initial?.alert_threshold_pct ?? 80);
    setEnabled(initial?.enabled ?? true);
  }, [open, initial, forceScope, allowedScopes]);

  const submit = async () => {
    const amount = Number(limitAmount);
    if (!Number.isFinite(amount) || amount <= 0) {
      toast.error(t("form.limitMustBePositive"));
      return;
    }
    if (scope === "agent" && !agentId) {
      toast.error(t("form.agentRequired"));
      return;
    }
    const pct =
      alertPct === "" ? null : Math.max(1, Math.min(100, Number(alertPct)));
    const payload = {
      name: name.trim(),
      scope,
      agent_id: scope === "agent" ? agentId : null,
      currency: currency.toUpperCase(),
      period,
      limit_amount: amount,
      alert_threshold_pct: pct,
      enabled,
    };
    try {
      if (mode === "create") {
        await create.mutateAsync(payload);
      } else {
        await update.mutateAsync(payload);
      }
      toast.success(t("saved"));
      onOpenChange(false);
    } catch (err) {
      toast.error(err instanceof Error ? err.message : t("saveFailed"));
    }
  };

  const busy = create.isPending || update.isPending;
  const canScopeSwitch = !forceScope && allowedScopes.length > 1;

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-[520px]">
        <DialogHeader>
          <DialogTitle>
            {mode === "create" ? t("newBudget") : t("editBudget")}
          </DialogTitle>
          <DialogDescription>{t("form.budgetDescription")}</DialogDescription>
        </DialogHeader>

        <div className="space-y-3">
          <div className="grid gap-1.5">
            <Label>{t("form.name")}</Label>
            <Input
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder={t("form.budgetNamePlaceholder")}
              data-testid="budget-form-name"
            />
          </div>

          {canScopeSwitch && (
            <div className="grid gap-1.5">
              <Label>{t("form.scope")}</Label>
              <Select
                value={scope}
                onValueChange={(v) => setScope(v as GovernanceScope)}
              >
                <SelectTrigger>
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {allowedScopes.map((s) => (
                    <SelectItem key={s} value={s}>
                      {t(`scope.${s}`)}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
          )}

          {scope === "agent" && (
            <div className="grid gap-1.5">
              <Label>{t("form.agent")}</Label>
              <Select value={agentId || "_"} onValueChange={(v) => setAgentId(v === "_" ? "" : v)}>
                <SelectTrigger>
                  <SelectValue placeholder={t("form.agentPlaceholder")} />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="_">{t("form.agentPlaceholder")}</SelectItem>
                  {agents.map((a) => (
                    <SelectItem key={a.id} value={a.id}>
                      {a.name}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
          )}

          <div className="grid grid-cols-2 gap-3">
            <div className="grid gap-1.5">
              <Label>{t("form.currency")}</Label>
              <Select value={currency} onValueChange={setCurrency}>
                <SelectTrigger>
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {CURRENCIES.map((c) => (
                    <SelectItem key={c} value={c}>
                      {c}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
            <div className="grid gap-1.5">
              <Label>{t("form.period")}</Label>
              <Select
                value={period}
                onValueChange={(v) => setPeriod(v as BudgetPeriod)}
              >
                <SelectTrigger>
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {PERIODS.map((p) => (
                    <SelectItem key={p} value={p}>
                      {t(`period.${p}`)}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
          </div>

          <div className="grid grid-cols-2 gap-3">
            <div className="grid gap-1.5">
              <Label>{t("form.limitAmount")}</Label>
              <Input
                type="number"
                min={0}
                step="0.01"
                value={limitAmount}
                onChange={(e) => setLimitAmount(e.target.value)}
                data-testid="budget-form-limit"
              />
            </div>
            <div className="grid gap-1.5">
              <Label>{t("form.alertPct")}</Label>
              <Input
                type="number"
                min={1}
                max={100}
                value={alertPct}
                onChange={(e) => {
                  const v = e.target.value;
                  setAlertPct(v === "" ? "" : Number(v));
                }}
              />
              <p className="text-[11px] sh-muted">{t("form.alertPctHint")}</p>
            </div>
          </div>

          <div className="flex items-center gap-2 pt-1">
            <Switch checked={enabled} onCheckedChange={setEnabled} />
            <span className="text-sm">
              {enabled ? tCommon("enabled") : tCommon("disabled")}
            </span>
          </div>
        </div>

        <DialogFooter>
          <Button variant="ghost" onClick={() => onOpenChange(false)}>
            {tCommon("cancel")}
          </Button>
          <Button
            onClick={submit}
            disabled={busy || !name.trim() || !limitAmount}
            data-testid="budget-form-submit"
          >
            {busy && <IconLoader2 className="size-4 animate-spin" />}
            {tCommon("save")}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
