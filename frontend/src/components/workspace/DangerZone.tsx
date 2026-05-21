"use client";

import { useState } from "react";
import { useRouter } from "@/lib/navigation";
import { IconAlertTriangle, IconLoader2 } from "@tabler/icons-react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useTranslations } from "next-intl";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
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
import { ApiError, api } from "@/lib/api";
import { useMe } from "@/hooks/use-me";
import { useActiveWorkspace } from "@/hooks/use-workspace";
import { useWorkspaceStore } from "@/stores/workspace-store";

export function DangerZone() {
  const t = useTranslations("workspace.dangerZone");
  const tCommon = useTranslations("common");
  const router = useRouter();
  const queryClient = useQueryClient();
  const { data: me } = useMe();
  const { data: workspace } = useActiveWorkspace();

  const [open, setOpen] = useState(false);
  const [confirmText, setConfirmText] = useState("");

  const handleOpenChange = (next: boolean) => {
    if (!next) setConfirmText("");
    setOpen(next);
  };

  const isOwner = me?.current_role === "owner";

  const deleteMutation = useMutation<void, unknown, void>({
    mutationFn: async () => {
      if (!workspace) throw new Error("no_workspace");
      await api.delete(`/api/v1/workspaces/${workspace.id}`);
    },
    onSuccess: () => {
      toast.success(t("deleted"));
      queryClient.invalidateQueries({ queryKey: ["me"] });
      queryClient.invalidateQueries({ queryKey: ["workspace"] });
      handleOpenChange(false);
      useWorkspaceStore.getState().clear();
      router.replace("/");
      if (typeof window !== "undefined") {
        window.location.reload();
      }
    },
    onError: (err) => {
      if (err instanceof ApiError) {
        toast.error(err.message);
      } else {
        toast.error(t("deleteFailed"));
      }
    },
  });

  if (!isOwner || !workspace) return null;

  const exactMatch = confirmText.trim() === workspace.name;

  return (
    <>
      <Card className="mt-6 border-red-500/40">
        <CardHeader>
          <CardTitle className="flex items-center gap-2 text-base text-red-600">
            <IconAlertTriangle className="size-4" />
            {t("title")}
          </CardTitle>
          <CardDescription>{t("description")}</CardDescription>
        </CardHeader>
        <CardContent>
          <Button variant="destructive" onClick={() => setOpen(true)}>
            {t("deleteCta")}
          </Button>
        </CardContent>
      </Card>

      <Dialog open={open} onOpenChange={handleOpenChange}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle className="flex items-center gap-2 text-red-600">
              <IconAlertTriangle className="size-4" />
              {t("confirmTitle")}
            </DialogTitle>
            <DialogDescription>
              {t("confirmBody", { name: workspace.name })}
            </DialogDescription>
          </DialogHeader>

          <div className="space-y-1.5">
            <Label htmlFor="danger-zone-confirm">
              {t("typeNameLabel", { name: workspace.name })}
            </Label>
            <Input
              id="danger-zone-confirm"
              value={confirmText}
              onChange={(e) => setConfirmText(e.target.value)}
              placeholder={workspace.name}
              autoComplete="off"
              autoFocus
            />
          </div>

          <DialogFooter>
            <Button variant="ghost" onClick={() => handleOpenChange(false)}>
              {tCommon("cancel")}
            </Button>
            <Button
              variant="destructive"
              onClick={() => deleteMutation.mutate()}
              disabled={!exactMatch || deleteMutation.isPending}
            >
              {deleteMutation.isPending && (
                <IconLoader2 className="size-4 animate-spin" />
              )}
              {t("confirmCta")}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  );
}
