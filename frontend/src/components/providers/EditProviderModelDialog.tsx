"use client";

import { useEffect, useState } from "react";
import { useTranslations } from "next-intl";
import { toast } from "sonner";
import {
  IconChevronDown,
  IconChevronRight,
  IconLoader2,
  IconLock,
} from "@tabler/icons-react";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
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
import { Switch } from "@/components/ui/switch";
import {
  useResolvedModelProfile,
  useUpdateProviderModel,
  type ProviderModelUpdate,
  type ReasoningEffort,
} from "@/hooks/use-providers";
import { CAPABILITY_META, CAP_DISPLAY_ORDER } from "./_modelMeta";
import {
  archetypeOf,
  reasoningFromArchetype,
  type Archetype,
} from "./reasoning-archetype";
import { cn } from "@/lib/utils";

interface Props {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  providerId: string;
  modelId: string;
  modelKey: string;
  initialLabel: string;
  initialContextWindow: number | null;
  initialCapabilities: string[];
}

type EffortSelection = "off" | ReasoningEffort;

interface ReasoningFormState {
  archetype: Archetype;
  effort: EffortSelection;
  default: "on" | "off";
  supportsEffort: boolean;
  toolCallSafe: boolean;
  flashAlternative: string;
}

const DEFAULT_STATE: ReasoningFormState = {
  archetype: "none",
  effort: "medium",
  default: "off",
  supportsEffort: false,
  toolCallSafe: true,
  flashAlternative: "",
};

const ARCHETYPE_ORDER: Archetype[] = ["none", "always", "hybrid"];
const EFFORT_ORDER: EffortSelection[] = ["off", "low", "medium", "high"];

export function EditProviderModelDialog({
  open,
  onOpenChange,
  providerId,
  modelId,
  modelKey,
  initialLabel,
  initialContextWindow,
  initialCapabilities,
}: Props) {
  const t = useTranslations("settings.providers.models");
  const tCommon = useTranslations("common");
  const tCap = useTranslations("settings.providers.models.capabilities");
  const tReasoning = useTranslations("settings.providers.modelDialog.reasoning");
  const update = useUpdateProviderModel(providerId);
  const resolved = useResolvedModelProfile(providerId, modelId, open);

  const [label, setLabel] = useState(initialLabel);
  const [ctx, setCtx] = useState<string>(
    initialContextWindow != null ? String(initialContextWindow) : "",
  );
  const [caps, setCaps] = useState<Set<string>>(
    () => new Set(initialCapabilities.map((c) => c.toLowerCase())),
  );
  const [form, setForm] = useState<ReasoningFormState>(DEFAULT_STATE);
  const [advancedOpen, setAdvancedOpen] = useState(false);
  const [overridden, setOverridden] = useState(false);

  useEffect(() => {
    if (!open) return;
    setLabel(initialLabel);
    setCtx(initialContextWindow != null ? String(initialContextWindow) : "");
    setCaps(new Set(initialCapabilities.map((c) => c.toLowerCase())));
    setAdvancedOpen(false);
    setOverridden(false);
  }, [open, initialLabel, initialContextWindow, initialCapabilities]);

  useEffect(() => {
    if (!resolved.data) return;
    const effort: EffortSelection =
      resolved.data.preferred_effort === "low" ||
      resolved.data.preferred_effort === "medium" ||
      resolved.data.preferred_effort === "high"
        ? resolved.data.preferred_effort
        : "off";
    setForm({
      archetype: archetypeOf(resolved.data.supported, resolved.data.hybrid),
      effort,
      default: resolved.data.default === "on" ? "on" : "off",
      supportsEffort: resolved.data.supports_effort,
      toolCallSafe: resolved.data.tool_call_safe,
      flashAlternative: resolved.data.flash_alternative ?? "",
    });
  }, [resolved.data]);

  // Builtin-recognized models open read-only so operators don't
  // accidentally break a curated catalog entry — the "Override" button
  // unlocks editing for the rare custom case.
  const isBuiltinLocked = resolved.data?.source === "builtin" && !overridden;

  function toggleCap(cap: string) {
    setCaps((prev) => {
      const next = new Set(prev);
      if (next.has(cap)) next.delete(cap);
      else next.add(cap);
      return next;
    });
  }

  function buildProfilePatch(): Record<string, unknown> {
    // The runner reads ``preferred_effort`` and applies it on top of
    // the builtin enable payload, so we don't need to ship raw
    // ``enable`` / ``disable`` wire payloads from this UI. Backend
    // ``_merge_reasoning`` falls back to the builtin enable/disable
    // when the override doesn't provide them.
    const { supported, hybrid } = reasoningFromArchetype(form.archetype);
    const reasoning: Record<string, unknown> = {
      supported,
      hybrid,
      default: hybrid ? form.default : supported ? "on" : "off",
      tool_call_safe: form.toolCallSafe,
      supports_effort: form.supportsEffort,
      preferred_effort:
        supported && form.supportsEffort && form.effort !== "off"
          ? form.effort
          : null,
    };
    const profile: Record<string, unknown> = { reasoning };
    const trimmedFlash = form.flashAlternative.trim();
    if (trimmedFlash) profile.flash_alternative = trimmedFlash;
    return profile;
  }

  async function submit() {
    const ctxNum = ctx.trim() ? Number(ctx.trim()) : null;
    if (ctxNum !== null && (!Number.isFinite(ctxNum) || ctxNum < 0)) {
      toast.error(t("errors.invalidContext"));
      return;
    }
    // Keep the ``reasoning`` capability badge in lockstep with the
    // chosen archetype so the model list reflects the same single
    // source of truth the runner reads.
    const nextCaps = new Set(caps);
    if (form.archetype === "none") nextCaps.delete("reasoning");
    else nextCaps.add("reasoning");
    const patch: ProviderModelUpdate = {
      label: label.trim() || null,
      context_window: ctxNum,
      capabilities: Array.from(nextCaps),
    };
    // A builtin-recognized row stays catalog-managed: only persist a
    // reasoning override once the operator explicitly unlocked it, so
    // editing the label alone doesn't freeze a stale profile snapshot.
    if (!isBuiltinLocked) {
      patch.metadata_json = { profile: buildProfilePatch() };
    }
    try {
      await update.mutateAsync({ modelId, patch });
      toast.success(t("editSuccess"));
      onOpenChange(false);
    } catch {
      toast.error(t("editFailed"));
    }
  }

  async function resetReasoningToDefaults() {
    try {
      await update.mutateAsync({
        modelId,
        patch: { metadata_json: { profile: null } },
      });
      toast.success(tReasoning("resetSuccess"));
      onOpenChange(false);
    } catch {
      toast.error(t("editFailed"));
    }
  }

  const reasoningLoading = resolved.isLoading;

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-md">
        <DialogHeader>
          <DialogTitle>{t("editTitle")}</DialogTitle>
          <DialogDescription>{t("editHint")}</DialogDescription>
        </DialogHeader>

        <div className="space-y-4">
          <div className="space-y-1.5">
            <Label className="text-sm">{t("modelId")}</Label>
            <div className="rounded-md border bg-muted/40 px-2.5 py-1.5 font-mono text-xs text-muted-foreground select-all">
              {modelKey}
            </div>
            <p className="text-[11px] text-muted-foreground">
              {t("modelIdHint")}
            </p>
          </div>

          <div className="space-y-1.5">
            <Label htmlFor="model-label" className="text-sm">
              {t("displayName")}
            </Label>
            <Input
              id="model-label"
              value={label}
              onChange={(e) => setLabel(e.target.value)}
              placeholder={t("displayNamePlaceholder")}
              autoFocus
            />
          </div>

          <div className="space-y-1.5">
            <Label htmlFor="model-ctx" className="text-sm">
              {t("contextWindow")}
            </Label>
            <Input
              id="model-ctx"
              type="number"
              inputMode="numeric"
              min={0}
              value={ctx}
              onChange={(e) => setCtx(e.target.value)}
              placeholder={t("contextWindowPlaceholder")}
            />
            <p className="text-[11px] text-muted-foreground">
              {t("contextWindowHint")}
            </p>
          </div>

          <div className="space-y-1.5">
            <Label className="text-sm">{t("capabilitiesLabel")}</Label>
            <div className="flex flex-wrap gap-1.5">
              {CAP_DISPLAY_ORDER.map((cap) => {
                const meta = CAPABILITY_META[cap];
                if (!meta) return null;
                const Icon = meta.icon;
                const isOn = caps.has(cap);
                return (
                  <button
                    key={cap}
                    type="button"
                    onClick={() => toggleCap(cap)}
                    className={cn(
                      "inline-flex items-center gap-1 rounded-full border px-2.5 py-1 text-[11px] font-medium transition",
                      isOn
                        ? "border-primary bg-primary/10 text-foreground"
                        : "border-border bg-card text-muted-foreground hover:border-foreground/40 hover:text-foreground",
                    )}
                    aria-pressed={isOn}
                  >
                    <Icon className="size-3" />
                    {tCap(meta.tooltipKey)}
                  </button>
                );
              })}
            </div>
            <p className="text-[11px] text-muted-foreground">
              {t("capabilitiesHint")}
            </p>
          </div>

          <div className="space-y-3 rounded-md border p-3">
            <div className="flex items-start justify-between gap-3">
              <div className="space-y-0.5">
                <Label className="text-sm font-medium">
                  {tReasoning("sectionTitle")}
                </Label>
                <p className="text-[11px] text-muted-foreground">
                  {tReasoning("archetypeHint")}
                </p>
              </div>
              {isBuiltinLocked ? (
                <Badge variant="outline" className="shrink-0 gap-1 text-[10px]">
                  <IconLock className="size-3" />
                  {tReasoning("lockedBadge")}
                </Badge>
              ) : null}
            </div>

            <div
              className="inline-flex w-full overflow-hidden rounded-md border bg-card"
              role="radiogroup"
              aria-label={tReasoning("archetypeLabel")}
            >
              {ARCHETYPE_ORDER.map((value) => {
                const isOn = form.archetype === value;
                return (
                  <button
                    key={value}
                    type="button"
                    role="radio"
                    aria-checked={isOn}
                    disabled={reasoningLoading || isBuiltinLocked}
                    onClick={() =>
                      setForm((prev) => ({ ...prev, archetype: value }))
                    }
                    className={cn(
                      "flex-1 px-2 py-1.5 text-[12px] font-medium transition disabled:cursor-not-allowed disabled:opacity-60",
                      isOn
                        ? "bg-primary/15 text-foreground"
                        : "text-muted-foreground hover:bg-muted/50",
                    )}
                  >
                    {tReasoning(`archetype.${value}`)}
                  </button>
                );
              })}
            </div>

            {isBuiltinLocked ? (
              <Button
                type="button"
                variant="outline"
                size="sm"
                className="w-full"
                onClick={() => setOverridden(true)}
              >
                {tReasoning("overrideCta")}
              </Button>
            ) : null}

            {form.archetype !== "none" ? (
              <div className="space-y-3 border-t pt-3">
                {form.supportsEffort ? (
                  <div className="space-y-1.5">
                    <Label className="text-[12px]">
                      {tReasoning("effortLabel")}
                    </Label>
                    <div
                      className="inline-flex w-full overflow-hidden rounded-md border bg-card"
                      role="radiogroup"
                    >
                      {EFFORT_ORDER.map((value) => {
                        const isOn = form.effort === value;
                        return (
                          <button
                            key={value}
                            type="button"
                            role="radio"
                            aria-checked={isOn}
                            disabled={isBuiltinLocked}
                            onClick={() =>
                              setForm((prev) => ({ ...prev, effort: value }))
                            }
                            className={cn(
                              "flex-1 px-2 py-1 text-[12px] font-medium transition disabled:cursor-not-allowed disabled:opacity-60",
                              isOn
                                ? "bg-primary/15 text-foreground"
                                : "text-muted-foreground hover:bg-muted/50",
                            )}
                          >
                            {tReasoning(`effort.${value}`)}
                          </button>
                        );
                      })}
                    </div>
                    <p className="text-[11px] text-muted-foreground">
                      {tReasoning("effortHint")}
                    </p>
                  </div>
                ) : null}

                {form.archetype === "hybrid" ? (
                  <div className="flex items-center justify-between gap-3">
                    <div className="space-y-0.5">
                      <Label className="text-[12px]">
                        {tReasoning("defaultLabel")}
                      </Label>
                      <p className="text-[11px] text-muted-foreground">
                        {tReasoning("defaultHint")}
                      </p>
                    </div>
                    <Select
                      value={form.default}
                      disabled={isBuiltinLocked}
                      onValueChange={(value) =>
                        setForm((prev) => ({
                          ...prev,
                          default: value === "on" ? "on" : "off",
                        }))
                      }
                    >
                      <SelectTrigger className="h-8 w-24 text-[12px]">
                        <SelectValue />
                      </SelectTrigger>
                      <SelectContent>
                        <SelectItem value="off">
                          {tReasoning("defaultOff")}
                        </SelectItem>
                        <SelectItem value="on">
                          {tReasoning("defaultOn")}
                        </SelectItem>
                      </SelectContent>
                    </Select>
                  </div>
                ) : null}
              </div>
            ) : null}

            <button
              type="button"
              onClick={() => setAdvancedOpen((v) => !v)}
              className="flex w-full items-center gap-1.5 border-t pt-2 text-left text-[11px] font-medium text-muted-foreground hover:text-foreground"
            >
              {advancedOpen ? (
                <IconChevronDown className="size-3.5" />
              ) : (
                <IconChevronRight className="size-3.5" />
              )}
              {tReasoning("advancedToggle")}
            </button>

            {advancedOpen ? (
              <div className="space-y-3 pt-1">
                <div className="flex items-start justify-between gap-3">
                  <div className="space-y-0.5">
                    <Label className="text-[12px]">
                      {tReasoning("toolCallSafeLabel")}
                    </Label>
                    <p className="text-[11px] text-muted-foreground">
                      {tReasoning("toolCallSafeHint")}
                    </p>
                  </div>
                  <Switch
                    checked={form.toolCallSafe}
                    disabled={form.archetype === "none" || isBuiltinLocked}
                    onCheckedChange={(v) =>
                      setForm((prev) => ({ ...prev, toolCallSafe: v }))
                    }
                  />
                </div>
                <div className="space-y-1.5">
                  <Label className="text-[12px]">
                    {tReasoning("flashAlternativeLabel")}
                  </Label>
                  <Input
                    value={form.flashAlternative}
                    onChange={(e) =>
                      setForm((prev) => ({
                        ...prev,
                        flashAlternative: e.target.value,
                      }))
                    }
                    placeholder={tReasoning("flashAlternativePlaceholder")}
                    className="h-8 text-[12px]"
                  />
                  <p className="text-[11px] text-muted-foreground">
                    {tReasoning("flashAlternativeHint")}
                  </p>
                </div>
                <div className="flex justify-end pt-1">
                  <Button
                    type="button"
                    variant="ghost"
                    size="sm"
                    onClick={() => void resetReasoningToDefaults()}
                    disabled={update.isPending}
                  >
                    {tReasoning("resetCta")}
                  </Button>
                </div>
              </div>
            ) : null}
          </div>
        </div>

        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)}>
            {tCommon("cancel")}
          </Button>
          <Button onClick={submit} disabled={update.isPending}>
            {update.isPending ? (
              <IconLoader2 className="size-4 animate-spin" />
            ) : null}
            {tCommon("save")}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
