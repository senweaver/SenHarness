"use client";

import { useEffect, useState } from "react";
import { useRouter } from "@/lib/navigation";
import { IconLoader2, IconPlus } from "@tabler/icons-react";
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
import { Textarea } from "@/components/ui/textarea";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import {
  useCreateFlow,
  type FlowTriggerKind,
} from "@/hooks/use-flows";

interface AddScheduleDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  agentId: string;
}

/**
 * AddScheduleDialog — schedules an agent run via the existing
 * Flow-cron primitive. We default to a 09:00 daily cron because
 * "daily standup at 9" is the most common case operators ask for;
 * everything else is a textual edit on top of that template.
 */
export function AddScheduleDialog({
  open,
  onOpenChange,
  agentId,
}: AddScheduleDialogProps) {
  const t = useTranslations("flows");
  const tForm = useTranslations("flows.form");
  const tCommon = useTranslations("common");
  const router = useRouter();
  const create = useCreateFlow();

  const [name, setName] = useState("");
  const [trigger, setTrigger] = useState<FlowTriggerKind>("cron");
  const [cron, setCron] = useState("0 9 * * *");
  const [prompt, setPrompt] = useState("Generate today's summary.");

  useEffect(() => {
    if (!open) {
      setName("");
      setTrigger("cron");
      setCron("0 9 * * *");
      setPrompt("Generate today's summary.");
    }
  }, [open]);

  const submit = async () => {
    if (!name.trim()) {
      toast.error(tForm("missingFields"));
      return;
    }
    try {
      const created = await create.mutateAsync({
        name: name.trim(),
        agent_id: agentId,
        trigger_kind: trigger,
        trigger_config: trigger === "cron" ? { cron, tz: "UTC" } : {},
        prompt_template: prompt.trim() || tForm("promptDefault"),
        enabled: true,
      });
      toast.success(t("detail.triggered"));
      onOpenChange(false);
      router.push(`/flows/${created.id}`);
    } catch {
      toast.error(t("detail.triggerFailed"));
    }
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-[600px]">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            <IconPlus className="size-4" />
            {t("new")}
          </DialogTitle>
          <DialogDescription>{t("description")}</DialogDescription>
        </DialogHeader>

        <div className="space-y-3">
          <div className="space-y-1.5">
            <Label>{tForm("name")}</Label>
            <Input
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder={tForm("namePlaceholder")}
            />
          </div>

          <div className="space-y-1.5">
            <Label>{tForm("trigger")}</Label>
            <Select
              value={trigger}
              onValueChange={(v) => setTrigger(v as FlowTriggerKind)}
            >
              <SelectTrigger>
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="cron">
                  {tForm("triggerName.cron")}
                </SelectItem>
                <SelectItem value="manual">
                  {tForm("triggerName.manual")}
                </SelectItem>
                <SelectItem value="webhook">
                  {tForm("triggerName.webhook")}
                </SelectItem>
              </SelectContent>
            </Select>
            <p className="text-[11px] sh-muted">
              {trigger === "cron"
                ? tForm("triggerDesc.cron")
                : trigger === "webhook"
                  ? tForm("triggerDesc.webhook")
                  : tForm("triggerDesc.manual")}
            </p>
          </div>

          {trigger === "cron" && (
            <div className="space-y-1.5">
              <Label>{tForm("cronExpr")}</Label>
              <Input
                value={cron}
                onChange={(e) => setCron(e.target.value)}
                className="font-mono text-[12px]"
              />
              <p className="text-[11px] sh-muted">{tForm("cronExprHint")}</p>
            </div>
          )}

          <div className="space-y-1.5">
            <Label>{tForm("prompt")}</Label>
            <Textarea
              value={prompt}
              onChange={(e) => setPrompt(e.target.value)}
              rows={4}
              placeholder={tForm("promptPlaceholder")}
              className="font-mono text-[12px]"
            />
            <p className="text-[11px] sh-muted">{tForm("promptHint")}</p>
          </div>
        </div>

        <DialogFooter>
          <Button variant="ghost" onClick={() => onOpenChange(false)}>
            {tCommon("cancel")}
          </Button>
          <Button onClick={() => void submit()} disabled={create.isPending}>
            {create.isPending && (
              <IconLoader2 className="size-4 animate-spin" />
            )}
            {tCommon("save")}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
