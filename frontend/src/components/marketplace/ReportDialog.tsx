"use client";

import { useState } from "react";
import { IconFlag, IconLoader2 } from "@tabler/icons-react";
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
  DialogTrigger,
} from "@/components/ui/dialog";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Textarea } from "@/components/ui/textarea";
import {
  type ReportReason,
  useReportAgent,
} from "@/hooks/use-moderation";

const REASONS: ReportReason[] = [
  "spam",
  "inappropriate",
  "copyright",
  "security",
  "misinformation",
  "other",
];

export function ReportDialog({
  agentId,
  trigger,
}: {
  agentId: string;
  trigger: React.ReactNode;
}) {
  const t = useTranslations("marketplace.report");
  const [open, setOpen] = useState(false);
  const [reason, setReason] = useState<ReportReason>("inappropriate");
  const [detail, setDetail] = useState("");
  const report = useReportAgent();

  const submit = async () => {
    try {
      await report.mutateAsync({
        agentId,
        reason,
        detail: detail.trim() || null,
      });
      toast.success(t("success"));
      setOpen(false);
      setDetail("");
    } catch {
      toast.error(t("failed"));
    }
  };

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger asChild>{trigger}</DialogTrigger>
      <DialogContent>
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            <IconFlag className="size-4 text-amber-500" />
            {t("title")}
          </DialogTitle>
          <DialogDescription>{t("description")}</DialogDescription>
        </DialogHeader>

        <div className="space-y-3">
          <div className="grid gap-1.5">
            <Label>{t("reasonLabel")}</Label>
            <Select
              value={reason}
              onValueChange={(v) => setReason(v as ReportReason)}
            >
              <SelectTrigger>
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {REASONS.map((r) => (
                  <SelectItem key={r} value={r}>
                    {t(`reason.${r}`)}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>

          <div className="grid gap-1.5">
            <Label htmlFor="detail">{t("detailLabel")}</Label>
            <Textarea
              id="detail"
              value={detail}
              onChange={(e) => setDetail(e.target.value)}
              placeholder={t("detailPlaceholder")}
              className="min-h-[100px]"
              maxLength={2000}
            />
            <p className="text-right text-[10px] sh-muted">
              {detail.length} / 2000
            </p>
          </div>
        </div>

        <DialogFooter>
          <Button
            variant="ghost"
            onClick={() => setOpen(false)}
            disabled={report.isPending}
          >
            {t("cancel")}
          </Button>
          <Button
            variant="destructive"
            onClick={submit}
            disabled={report.isPending}
          >
            {report.isPending && (
              <IconLoader2 className="size-4 animate-spin" />
            )}
            {t("submit")}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
