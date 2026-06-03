"use client";

import { useState } from "react";
import { IconLoader2, IconPlus, IconTrash } from "@tabler/icons-react";
import { useTranslations } from "next-intl";
import { toast } from "sonner";

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
import { useAgents } from "@/hooks/use-agents";
import {
  type BindingMatchScope,
  useChannelBindings,
  useCreateChannelBinding,
  useDeleteChannelBinding,
} from "@/hooks/use-channels";
import {
  requiresMatchValue,
  sortBindingsBySpecificity,
} from "@/lib/channel-routing";

/**
 * Layered binding-rules editor (P1 "most-specific-wins"). Authors the
 * subset of match scopes the resolver actually matches today — peer /
 * group / channel-default — and renders existing rules in resolution
 * order. Empty list ⇒ the channel-level default still wins (P0).
 */
const EDITABLE_SCOPES: BindingMatchScope[] = ["peer", "group", "channel_default"];

interface ChannelBindingRulesProps {
  channelId: string;
}

export function ChannelBindingRules({ channelId }: ChannelBindingRulesProps) {
  const t = useTranslations("settings.channels.bindings");
  const { data: bindings, isLoading } = useChannelBindings(channelId);
  const { data: agents } = useAgents();
  const create = useCreateChannelBinding(channelId);
  const remove = useDeleteChannelBinding(channelId);

  const [scope, setScope] = useState<BindingMatchScope>("peer");
  const [matchValue, setMatchValue] = useState("");
  const [targetAgentId, setTargetAgentId] = useState<string>("");

  const ordered = sortBindingsBySpecificity(bindings ?? []);
  const agentName = (id: string | null) =>
    (agents ?? []).find((a) => a.id === id)?.name ?? id ?? "—";

  const add = async () => {
    if (requiresMatchValue(scope) && !matchValue.trim()) {
      toast.error(t("matchValueRequired"));
      return;
    }
    if (!targetAgentId) {
      toast.error(t("targetRequired"));
      return;
    }
    try {
      await create.mutateAsync({
        match_scope: scope,
        match_value: requiresMatchValue(scope) ? matchValue.trim() : null,
        target_agent_id: targetAgentId,
        priority: 0,
      });
      setMatchValue("");
      toast.success(t("added"));
    } catch {
      toast.error(t("addFailed"));
    }
  };

  const del = async (id: string) => {
    try {
      await remove.mutateAsync(id);
      toast.success(t("removed"));
    } catch {
      toast.error(t("removeFailed"));
    }
  };

  return (
    <div className="space-y-2 rounded-md border bg-[rgb(var(--color-card))] p-3">
      <div>
        <Label className="text-[12px] font-medium">{t("title")}</Label>
        <p className="mt-0.5 text-[11px] sh-muted">{t("description")}</p>
      </div>

      {isLoading ? (
        <IconLoader2 className="size-4 animate-spin sh-muted" />
      ) : ordered.length === 0 ? (
        <p className="text-[11px] sh-muted">{t("empty")}</p>
      ) : (
        <ul className="space-y-1">
          {ordered.map((b) => (
            <li
              key={b.id}
              className="flex items-center justify-between gap-2 rounded-md border px-2 py-1 text-[12px]"
            >
              <span className="min-w-0 truncate">
                <span className="font-medium">
                  {t(`scope.${b.match_scope}`)}
                </span>
                {requiresMatchValue(b.match_scope) && (
                  <span className="sh-muted"> · {b.match_value}</span>
                )}
                <span className="sh-muted">
                  {" "}
                  → {agentName(b.target_agent_id)}
                </span>
              </span>
              <Button
                type="button"
                size="icon"
                variant="ghost"
                className="size-7 shrink-0"
                onClick={() => del(b.id)}
                disabled={remove.isPending}
                title={t("remove")}
              >
                <IconTrash className="size-3.5" />
              </Button>
            </li>
          ))}
        </ul>
      )}

      <div className="space-y-1.5 border-t pt-2">
        <div className="flex gap-1.5">
          <Select
            value={scope}
            onValueChange={(v) => setScope(v as BindingMatchScope)}
          >
            <SelectTrigger className="w-[130px]">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {EDITABLE_SCOPES.map((s) => (
                <SelectItem key={s} value={s}>
                  {t(`scope.${s}`)}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
          {requiresMatchValue(scope) && (
            <Input
              value={matchValue}
              placeholder={t("matchValuePlaceholder")}
              onChange={(e) => setMatchValue(e.target.value)}
              className="flex-1"
            />
          )}
        </div>
        <div className="flex gap-1.5">
          <Select value={targetAgentId} onValueChange={setTargetAgentId}>
            <SelectTrigger className="flex-1">
              <SelectValue placeholder={t("targetPlaceholder")} />
            </SelectTrigger>
            <SelectContent>
              {(agents ?? []).map((a) => (
                <SelectItem key={a.id} value={a.id}>
                  {a.name}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
          <Button
            type="button"
            size="sm"
            variant="outline"
            onClick={add}
            disabled={create.isPending}
          >
            {create.isPending ? (
              <IconLoader2 className="size-3.5 animate-spin" />
            ) : (
              <IconPlus className="size-3.5" />
            )}
            {t("add")}
          </Button>
        </div>
      </div>
    </div>
  );
}

export default ChannelBindingRules;
