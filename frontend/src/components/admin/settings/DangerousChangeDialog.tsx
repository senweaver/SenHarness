"use client";

import { IconAlertTriangle } from "@tabler/icons-react";
import { useTranslations } from "next-intl";

import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";

export function DangerousChangeDialog({
  open,
  onOpenChange,
  fields,
  onConfirm,
  loading,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  fields: string[];
  onConfirm: () => void;
  loading?: boolean;
}) {
  const t = useTranslations("platformSettings");
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-md">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2 text-amber-600 dark:text-amber-400">
            <IconAlertTriangle className="size-5" />
            {t("dangerousChangeTitle")}
          </DialogTitle>
          <DialogDescription>{t("dangerousChangeBody")}</DialogDescription>
        </DialogHeader>

        <ul className="my-3 list-disc space-y-1 pl-6 text-sm">
          {fields.map((f) => (
            <li key={f} className="font-mono">
              {f}
            </li>
          ))}
        </ul>

        <DialogFooter>
          <Button
            variant="outline"
            onClick={() => onOpenChange(false)}
            disabled={loading}
          >
            {t("dangerousChangeCancel")}
          </Button>
          <Button
            variant="destructive"
            onClick={onConfirm}
            disabled={loading}
          >
            {t("dangerousChangeConfirm")}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
