"use client";

import { useEffect, useState } from "react";
import { useTranslations } from "next-intl";
import { toast } from "sonner";
import {
  IconChevronDown,
  IconChevronRight,
  IconLoader2,
} from "@tabler/icons-react";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
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
  type ReasoningEffort,
} from "@/hooks/use-providers";
import { CAPABILITY_META, CAP_DISPLAY_ORDER } from "./_modelMeta";
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
  supported: boolean;
  effort: EffortSelection;
  default: "on" | "off";
  hybrid: boolean;
  toolCallSafe: boolean;
  flashAlternative: string;
}

const DEFAULT_STATE: ReasoningFormState = {
  supported: false,
  effort: "medium",
  default: "off",
  hybrid: false,
  toolCallSafe: true,
  flashAlternative: "",
};

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

  useEffect(() => {
    if (!open) return;
    setLabel(initialLabel);
    setCtx(initialContextWindow != null ? String(initialContextWindow) : "");
    setCaps(new Set(initialCapabilities.map((c) => c.toLowerCase())));
    setAdvancedOpen(false);
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
      supported: resolved.data.supported,
      effort,
      default: resolved.data.default === "on" ? "on" : "off",
      hybrid: resolved.data.hybrid,
      toolCallSafe: resolved.data.tool_call_safe,
      flashAlternative: resolved.data.flash_alternative ?? "",
    });
  }, [resolved.data]);

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
    const reasoning: Record<string, unknown> = {
      supported: form.supported,
      hybrid: form.hybrid,
      default: form.default,
      tool_call_safe: form.toolCallSafe,
      preferred_effort:
        form.supported && form.effort !== "off" ? form.effort : null,
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
    try {
      await update.mutateAsync({
        modelId,
        patch: {
          label: label.trim() || null,
          context_window: ctxNum,
          capabilities: Array.from(caps),
          metadata_json: { profile: buildProfilePatch() },
        },
      });
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
                  {tReasoning("supportedHint")}
                </p>
              </div>
              <Switch
                checked={form.supported}
                disabled={reasoningLoading}
                onCheckedChange={(v) =>
                  setForm((prev) => ({ ...prev, supported: v }))
                }
              />
            </div>

            {form.supported ? (
              <div className="space-y-3 border-t pt-3">
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
                          onClick={() =>
                            setForm((prev) => ({ ...prev, effort: value }))
                          }
                          className={cn(
                            "flex-1 px-2 py-1 text-[12px] font-medium transition",
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

                {form.hybrid ? (
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
                      {tReasoning("hybridLabel")}
                    </Label>
                    <p className="text-[11px] text-muted-foreground">
                      {tReasoning("hybridHint")}
                    </p>
                  </div>
                  <Switch
                    checked={form.hybrid}
                    disabled={!form.supported}
                    onCheckedChange={(v) =>
                      setForm((prev) => ({ ...prev, hybrid: v }))
                    }
                  />
                </div>
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
                    disabled={!form.supported}
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
