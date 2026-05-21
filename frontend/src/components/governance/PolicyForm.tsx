"use client";

/**
 * PolicyForm — Dialog with create + edit modes for governance policies.
 *
 * Shown from `PolicyList` ("New" button and each row's edit action). The
 * form exposes every field backing `app/schemas/governance.py::PolicyCreate`
 * so workspace admins can use it, while the server still enforces that
 * `scope=global` requires platform_admin.
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
import { Textarea } from "@/components/ui/textarea";
import { useAgents } from "@/hooks/use-agents";
import {
  type GovernanceScope,
  type PolicyRead,
  useCreatePolicy,
  useUpdatePolicy,
} from "@/hooks/use-governance";

interface PolicyFormProps {
  mode: "create" | "edit";
  open: boolean;
  onOpenChange: (open: boolean) => void;
  initial?: PolicyRead;
  /** Scope choices the caller allows. Omit GLOBAL for workspace-admin views. */
  allowedScopes: GovernanceScope[];
  /** Force a scope (hides the select, used when caller only wants a single scope). */
  forceScope?: GovernanceScope;
}

const DEFAULT_RULES_HINT = `{\n  "max_cost_usd_per_session": 1.0,\n  "shields": {\n    "tool_access": {\n      "deny": ["execute", "delete_file"]\n    }\n  }\n}`;

export function PolicyForm({
  mode,
  open,
  onOpenChange,
  initial,
  allowedScopes,
  forceScope,
}: PolicyFormProps) {
  const t = useTranslations("settings.governance");
  const tCommon = useTranslations("common");
  const create = useCreatePolicy();
  const update = useUpdatePolicy(initial?.id ?? "");
  const agentsQ = useAgents();
  const agents = agentsQ.data ?? [];

  const [name, setName] = useState(initial?.name ?? "");
  const [description, setDescription] = useState(initial?.description ?? "");
  const [scope, setScope] = useState<GovernanceScope>(
    forceScope ?? initial?.scope ?? allowedScopes[0] ?? "workspace",
  );
  const [agentId, setAgentId] = useState<string>(initial?.agent_id ?? "");
  const [priority, setPriority] = useState<number>(initial?.priority ?? 100);
  const [enabled, setEnabled] = useState<boolean>(initial?.enabled ?? true);
  const [rulesText, setRulesText] = useState<string>(
    initial ? JSON.stringify(initial.rules_json, null, 2) : "",
  );
  const [rulesError, setRulesError] = useState<string | null>(null);

  useEffect(() => {
    if (!open) return;
    setName(initial?.name ?? "");
    setDescription(initial?.description ?? "");
    setScope(forceScope ?? initial?.scope ?? allowedScopes[0] ?? "workspace");
    setAgentId(initial?.agent_id ?? "");
    setPriority(initial?.priority ?? 100);
    setEnabled(initial?.enabled ?? true);
    setRulesText(initial ? JSON.stringify(initial.rules_json, null, 2) : "");
    setRulesError(null);
  }, [open, initial, forceScope, allowedScopes]);

  const submit = async () => {
    let rules: Record<string, unknown> = {};
    if (rulesText.trim()) {
      try {
        rules = JSON.parse(rulesText) as Record<string, unknown>;
        if (typeof rules !== "object" || Array.isArray(rules)) {
          setRulesError(t("form.rulesMustBeObject"));
          return;
        }
      } catch (err) {
        setRulesError(
          err instanceof Error ? err.message : t("form.rulesInvalidJson"),
        );
        return;
      }
    }
    if (scope === "agent" && !agentId) {
      toast.error(t("form.agentRequired"));
      return;
    }
    const payload = {
      name: name.trim(),
      description: description.trim() || null,
      scope,
      agent_id: scope === "agent" ? agentId : null,
      priority,
      enabled,
      rules_json: rules,
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
      <DialogContent className="sm:max-w-[560px]">
        <DialogHeader>
          <DialogTitle>
            {mode === "create" ? t("newPolicy") : t("editPolicy")}
          </DialogTitle>
          <DialogDescription>{t("form.description")}</DialogDescription>
        </DialogHeader>

        <div className="space-y-3">
          <div className="grid gap-1.5">
            <Label>{t("form.name")}</Label>
            <Input
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder={t("form.namePlaceholder")}
              data-testid="policy-form-name"
            />
          </div>

          <div className="grid gap-1.5">
            <Label>{t("form.descriptionLabel")}</Label>
            <Textarea
              value={description}
              onChange={(e) => setDescription(e.target.value)}
              className="min-h-[60px]"
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
              <p className="text-[11px] sh-muted">{t(`scope.${scope}Desc`)}</p>
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
              <Label>{t("form.priority")}</Label>
              <Input
                type="number"
                min={0}
                max={10000}
                value={priority}
                onChange={(e) =>
                  setPriority(Math.max(0, Math.min(10000, Number(e.target.value) || 0)))
                }
              />
              <p className="text-[11px] sh-muted">{t("form.priorityHint")}</p>
            </div>
            <div className="grid gap-1.5">
              <Label>{t("form.enabled")}</Label>
              <div className="flex items-center gap-2 pt-2">
                <Switch checked={enabled} onCheckedChange={setEnabled} />
                <span className="text-sm">{enabled ? tCommon("enabled") : tCommon("disabled")}</span>
              </div>
            </div>
          </div>

          <div className="grid gap-1.5">
            <Label>{t("form.rules")}</Label>
            <Textarea
              value={rulesText}
              onChange={(e) => {
                setRulesText(e.target.value);
                setRulesError(null);
              }}
              placeholder={DEFAULT_RULES_HINT}
              className="min-h-[160px] font-mono text-[12px]"
            />
            {rulesError && (
              <p className="text-[11px] text-destructive">{rulesError}</p>
            )}
            <p className="text-[11px] sh-muted">{t("form.rulesHint")}</p>
          </div>
        </div>

        <DialogFooter>
          <Button variant="ghost" onClick={() => onOpenChange(false)}>
            {tCommon("cancel")}
          </Button>
          <Button
            onClick={submit}
            disabled={busy || !name.trim()}
            data-testid="policy-form-submit"
          >
            {busy && <IconLoader2 className="size-4 animate-spin" />}
            {tCommon("save")}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
