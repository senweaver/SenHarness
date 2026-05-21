"use client";

import { useEffect, useState } from "react";
import { useTranslations } from "next-intl";
import { toast } from "sonner";
import { IconLoader2 } from "@tabler/icons-react";
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
import { useUpdateProviderModel } from "@/hooks/use-providers";
import { CAPABILITY_META, CAP_DISPLAY_ORDER } from "./_modelMeta";
import { cn } from "@/lib/utils";

interface Props {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  providerId: string;
  modelId: string;
  modelKey: string; // the immutable model id (e.g. "gpt-4.1")
  initialLabel: string;
  initialContextWindow: number | null;
  initialCapabilities: string[];
}

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
  const update = useUpdateProviderModel(providerId);

  const [label, setLabel] = useState(initialLabel);
  const [ctx, setCtx] = useState<string>(
    initialContextWindow != null ? String(initialContextWindow) : "",
  );
  const [caps, setCaps] = useState<Set<string>>(
    () => new Set(initialCapabilities.map((c) => c.toLowerCase())),
  );

  useEffect(() => {
    if (!open) return;
    setLabel(initialLabel);
    setCtx(initialContextWindow != null ? String(initialContextWindow) : "");
    setCaps(new Set(initialCapabilities.map((c) => c.toLowerCase())));
  }, [open, initialLabel, initialContextWindow, initialCapabilities]);

  function toggleCap(cap: string) {
    setCaps((prev) => {
      const next = new Set(prev);
      if (next.has(cap)) next.delete(cap);
      else next.add(cap);
      return next;
    });
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
        },
      });
      toast.success(t("editSuccess"));
      onOpenChange(false);
    } catch {
      toast.error(t("editFailed"));
    }
  }

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
