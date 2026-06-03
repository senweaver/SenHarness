"use client";

import { useState } from "react";
import { IconChevronDown, IconPlus, IconTrash } from "@tabler/icons-react";
import { useTranslations } from "next-intl";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { useAgentTerm } from "@/components/nav/AgentTermLabel";
import type {
  ChannelHandoffRule,
  ChannelPolicy,
  ChannelRoutingConfig,
  GroupOverride,
  HandoffMode,
  MenuStyle,
  ReplyAttribution,
} from "@/hooks/use-channels";
import { useSquads } from "@/hooks/use-squads";
import { useWorkspaceStore } from "@/stores/workspace-store";
import type { AgentRead } from "@/types/api";
import { joinKeywords, parseKeywords } from "@/lib/channel-routing";
import { cn } from "@/lib/utils";

/** Sentinel for "the channel's own workspace" (backend: scope_ref_id=null). */
const OWN_WORKSPACE = "__own__";

const POLICY_VALUES: ChannelPolicy[] = [
  "open",
  "allowlist",
  "disabled",
  "pairing",
];
const MENU_STYLE_VALUES: MenuStyle[] = ["auto", "text", "buttons"];
const ATTRIBUTION_VALUES: ReplyAttribution[] = ["prefix", "identity", "off"];
const GROUP_OVERRIDE_VALUES: GroupOverride[] = ["shared", "per_sender"];
const HANDOFF_MODE_VALUES: HandoffMode[] = ["switch", "suggest"];

/** Sensible defaults — mirror ``ChannelRoutingConfig`` backend defaults. */
export const DEFAULT_ROUTING_CONFIG: ChannelRoutingConfig = {
  bind_scope: "agent",
  scope_ref_id: null,
  allowlist_agent_ids: null,
  dm_policy: "open",
  group_policy: "disabled",
  menu_style: "auto",
  selection_window_seconds: 300,
  reply_attribution: "prefix",
  group_override: "shared",
  handoff_rules: [],
};

interface ChannelRoutingFieldsProps {
  value: ChannelRoutingConfig;
  onChange: (next: ChannelRoutingConfig) => void;
  agents: AgentRead[] | undefined;
}

export function ChannelRoutingFields({
  value,
  onChange,
  agents,
}: ChannelRoutingFieldsProps) {
  const t = useTranslations("settings.channels.routing");
  const term = useAgentTerm();
  const workspaces = useWorkspaceStore((s) => s.workspaces);
  const { data: squads } = useSquads();
  const [advancedOpen, setAdvancedOpen] = useState(false);

  const patch = (next: Partial<ChannelRoutingConfig>) =>
    onChange({ ...value, ...next });

  const selectedAgents = value.allowlist_agent_ids ?? [];
  const toggleAgent = (id: string) => {
    const present = selectedAgents.includes(id);
    const next = present
      ? selectedAgents.filter((a) => a !== id)
      : [...selectedAgents, id];
    patch({ allowlist_agent_ids: next.length > 0 ? next : null });
  };

  return (
    <div className="space-y-3">
      <div className="grid gap-1.5">
        <Label className="text-[12px] font-medium">{t("scope.label")}</Label>
        <Select
          value={value.bind_scope}
          onValueChange={(v) =>
            patch({ bind_scope: v as ChannelRoutingConfig["bind_scope"] })
          }
        >
          <SelectTrigger>
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="agent">{t("scope.agent", { term })}</SelectItem>
            <SelectItem value="workspace">{t("scope.workspace")}</SelectItem>
            <SelectItem value="user">{t("scope.user")}</SelectItem>
            <SelectItem value="squad">{t("scope.squad")}</SelectItem>
          </SelectContent>
        </Select>
        <p className="text-[11px] sh-muted">
          {t(`scopeHint.${value.bind_scope}`, { term })}
        </p>
      </div>

      {value.bind_scope === "workspace" && (
        <div className="grid gap-1.5">
          <Label className="text-[12px]">{t("scopeRef.label")}</Label>
          <Select
            value={value.scope_ref_id ?? OWN_WORKSPACE}
            onValueChange={(v) =>
              patch({ scope_ref_id: v === OWN_WORKSPACE ? null : v })
            }
          >
            <SelectTrigger>
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value={OWN_WORKSPACE}>
                {t("scopeRef.own")}
              </SelectItem>
              {workspaces.map((w) => (
                <SelectItem key={w.id} value={w.id}>
                  {w.name}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
          <p className="text-[11px] sh-muted">{t("scopeRef.hint")}</p>
        </div>
      )}

      {value.bind_scope === "squad" && (
        <div className="grid gap-1.5">
          <Label className="text-[12px]">{t("squadRef.label")}</Label>
          <Select
            value={value.scope_ref_id ?? ""}
            onValueChange={(v) => patch({ scope_ref_id: v })}
          >
            <SelectTrigger>
              <SelectValue placeholder={t("squadRef.placeholder")} />
            </SelectTrigger>
            <SelectContent>
              {(squads ?? []).map((s) => (
                <SelectItem key={s.id} value={s.id}>
                  {s.name}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
          {(squads ?? []).length === 0 && (
            <Badge variant="outline">{t("squadRef.empty")}</Badge>
          )}
          <p className="text-[11px] sh-muted">{t("squadRef.hint")}</p>
        </div>
      )}

      <div className="grid gap-1.5">
        <Label className="text-[12px]">{t("allowlist.label")}</Label>
        <div className="flex flex-wrap gap-1.5">
          {(agents ?? []).map((a) => {
            const active = selectedAgents.includes(a.id);
            return (
              <button
                key={a.id}
                type="button"
                onClick={() => toggleAgent(a.id)}
                aria-pressed={active}
                className={cn(
                  "rounded-md border px-2 py-1 text-[12px] transition",
                  "hover:border-[rgb(var(--color-primary))]",
                  active &&
                    "border-[rgb(var(--color-primary))] bg-[rgb(var(--color-primary))]/10",
                )}
              >
                {a.name}
              </button>
            );
          })}
          {(agents ?? []).length === 0 && (
            <Badge variant="outline">{t("allowlist.empty")}</Badge>
          )}
        </div>
        <p className="text-[11px] sh-muted">{t("allowlist.hint")}</p>
      </div>

      <div className="rounded-md border bg-[rgb(var(--color-card))]">
        <button
          type="button"
          onClick={() => setAdvancedOpen((v) => !v)}
          className="flex w-full items-center gap-2 px-3 py-2 text-left text-xs sh-muted"
          aria-expanded={advancedOpen}
        >
          <IconChevronDown
            className={cn(
              "size-3.5 transition-transform",
              advancedOpen && "rotate-180",
            )}
          />
          <span>{t("advanced.toggle")}</span>
        </button>
        {advancedOpen && (
          <div className="space-y-3 border-t px-3 py-3">
            <PolicySelect
              label={t("dmPolicy.label")}
              hint={t("dmPolicy.hint")}
              value={value.dm_policy}
              onChange={(v) => patch({ dm_policy: v })}
            />
            <PolicySelect
              label={t("groupPolicy.label")}
              hint={t("groupPolicy.hint")}
              value={value.group_policy}
              onChange={(v) => patch({ group_policy: v })}
            />
            <div className="grid gap-1.5">
              <Label className="text-[12px]">{t("menuStyle.label")}</Label>
              <Select
                value={value.menu_style}
                onValueChange={(v) => patch({ menu_style: v as MenuStyle })}
              >
                <SelectTrigger>
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {MENU_STYLE_VALUES.map((v) => (
                    <SelectItem key={v} value={v}>
                      {t(`menuStyle.option.${v}`)}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
            <div className="grid gap-1.5">
              <Label className="text-[12px]">{t("attribution.label")}</Label>
              <Select
                value={value.reply_attribution}
                onValueChange={(v) =>
                  patch({ reply_attribution: v as ReplyAttribution })
                }
              >
                <SelectTrigger>
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {ATTRIBUTION_VALUES.map((v) => (
                    <SelectItem key={v} value={v}>
                      {t(`attribution.option.${v}`)}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
            <div className="grid gap-1.5">
              <Label className="text-[12px]">
                {t("selectionWindow.label")}
              </Label>
              <Input
                type="number"
                min={0}
                max={86400}
                value={value.selection_window_seconds}
                onChange={(e) =>
                  patch({
                    selection_window_seconds: clampWindow(e.target.value),
                  })
                }
              />
              <p className="text-[11px] sh-muted">
                {t("selectionWindow.hint")}
              </p>
            </div>
            <div className="grid gap-1.5">
              <Label className="text-[12px]">{t("groupOverride.label")}</Label>
              <Select
                value={value.group_override}
                onValueChange={(v) =>
                  patch({ group_override: v as GroupOverride })
                }
              >
                <SelectTrigger>
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {GROUP_OVERRIDE_VALUES.map((v) => (
                    <SelectItem key={v} value={v}>
                      {t(`groupOverride.option.${v}`)}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
              <p className="text-[11px] sh-muted">
                {t(`groupOverride.hint.${value.group_override}`)}
              </p>
            </div>
            <HandoffRulesEditor
              rules={value.handoff_rules ?? []}
              onChange={(handoff_rules) => patch({ handoff_rules })}
              agents={agents}
              term={term}
            />
          </div>
        )}
      </div>
    </div>
  );
}

/**
 * Minimal handoff-rule editor: each row is a comma-separated keyword
 * list + a target {term} + a mode (switch / suggest). The backend keyword
 * router (deterministic, case-insensitive substring) consumes these.
 */
function HandoffRulesEditor({
  rules,
  onChange,
  agents,
  term,
}: {
  rules: ChannelHandoffRule[];
  onChange: (next: ChannelHandoffRule[]) => void;
  agents: AgentRead[] | undefined;
  term: string;
}) {
  const t = useTranslations("settings.channels.routing");

  const update = (idx: number, patch: Partial<ChannelHandoffRule>) =>
    onChange(rules.map((r, i) => (i === idx ? { ...r, ...patch } : r)));
  const remove = (idx: number) => onChange(rules.filter((_, i) => i !== idx));
  const add = () =>
    onChange([...rules, { keywords: [], target: "", mode: "switch" }]);

  return (
    <div className="grid gap-1.5">
      <Label className="text-[12px]">{t("handoff.label")}</Label>
      <p className="text-[11px] sh-muted">{t("handoff.hint", { term })}</p>
      <div className="space-y-2">
        {rules.map((rule, idx) => (
          <div
            key={idx}
            className="space-y-1.5 rounded-md border bg-[rgb(var(--color-card))] p-2"
          >
            <Input
              value={joinKeywords(rule.keywords)}
              placeholder={t("handoff.keywordsPlaceholder")}
              onChange={(e) =>
                update(idx, { keywords: parseKeywords(e.target.value) })
              }
            />
            <div className="flex gap-1.5">
              <Select
                value={rule.target}
                onValueChange={(v) => update(idx, { target: v })}
              >
                <SelectTrigger className="flex-1">
                  <SelectValue placeholder={t("handoff.targetPlaceholder")} />
                </SelectTrigger>
                <SelectContent>
                  {(agents ?? []).map((a) => (
                    <SelectItem key={a.id} value={a.name}>
                      {a.name}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
              <Select
                value={rule.mode}
                onValueChange={(v) => update(idx, { mode: v as HandoffMode })}
              >
                <SelectTrigger className="w-[120px]">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {HANDOFF_MODE_VALUES.map((m) => (
                    <SelectItem key={m} value={m}>
                      {t(`handoff.mode.${m}`)}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
              <Button
                type="button"
                size="icon"
                variant="ghost"
                className="size-9 shrink-0"
                onClick={() => remove(idx)}
                title={t("handoff.remove")}
              >
                <IconTrash className="size-3.5" />
              </Button>
            </div>
          </div>
        ))}
        <Button type="button" size="sm" variant="outline" onClick={add}>
          <IconPlus className="size-3.5" />
          {t("handoff.add")}
        </Button>
      </div>
    </div>
  );
}

function clampWindow(raw: string): number {
  const parsed = Number.parseInt(raw, 10);
  if (Number.isNaN(parsed)) return 0;
  return Math.min(86400, Math.max(0, parsed));
}

function PolicySelect({
  label,
  hint,
  value,
  onChange,
}: {
  label: string;
  hint: string;
  value: ChannelPolicy;
  onChange: (v: ChannelPolicy) => void;
}) {
  const t = useTranslations("settings.channels.routing");
  return (
    <div className="grid gap-1.5">
      <Label className="text-[12px]">{label}</Label>
      <Select value={value} onValueChange={(v) => onChange(v as ChannelPolicy)}>
        <SelectTrigger>
          <SelectValue />
        </SelectTrigger>
        <SelectContent>
          {POLICY_VALUES.map((v) => (
            <SelectItem key={v} value={v}>
              {t(`policy.${v}`)}
            </SelectItem>
          ))}
        </SelectContent>
      </Select>
      <p className="text-[11px] sh-muted">{hint}</p>
    </div>
  );
}

export default ChannelRoutingFields;
