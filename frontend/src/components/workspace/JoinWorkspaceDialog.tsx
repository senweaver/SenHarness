"use client";

import { useState } from "react";
import { IconLoader2 } from "@tabler/icons-react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
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
import { ApiError, api } from "@/lib/api";
import { switchActiveWorkspace } from "@/lib/workspace";

interface JoinWorkspaceDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
}

interface AcceptOut {
  workspace_id: string;
  role: string;
}

export function JoinWorkspaceDialog({
  open,
  onOpenChange,
}: JoinWorkspaceDialogProps) {
  const t = useTranslations("workspaceSwitcher.joinDialog");
  const tCommon = useTranslations("common");
  const queryClient = useQueryClient();
  const [code, setCode] = useState("");

  const handleOpenChange = (next: boolean) => {
    if (!next) setCode("");
    onOpenChange(next);
  };

  const accept = useMutation<AcceptOut, unknown, void>({
    mutationFn: () =>
      api.post<AcceptOut>("/api/v1/workspaces/invitations/accept", {
        code: code.trim(),
      }),
    onSuccess: async (result) => {
      toast.success(t("joined"));
      queryClient.invalidateQueries({ queryKey: ["me"] });
      handleOpenChange(false);
      const switched = await switchActiveWorkspace(result.workspace_id);
      if (switched && typeof window !== "undefined") {
        window.location.reload();
      }
    },
    onError: (err) => {
      if (err instanceof ApiError) {
        toast.error(err.message);
      } else {
        toast.error(t("joinFailed"));
      }
    },
  });

  const valid = code.trim().length > 0;

  return (
    <Dialog open={open} onOpenChange={handleOpenChange}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>{t("title")}</DialogTitle>
          <DialogDescription>{t("description")}</DialogDescription>
        </DialogHeader>

        <div className="space-y-1.5">
          <Label htmlFor="workspace-join-code">{t("codeLabel")}</Label>
          <Input
            id="workspace-join-code"
            value={code}
            onChange={(e) => setCode(e.target.value)}
            placeholder={t("codePlaceholder")}
            autoFocus
          />
        </div>

        <DialogFooter>
          <Button variant="ghost" onClick={() => handleOpenChange(false)}>
            {tCommon("cancel")}
          </Button>
          <Button
            onClick={() => accept.mutate()}
            disabled={!valid || accept.isPending}
          >
            {accept.isPending && (
              <IconLoader2 className="size-4 animate-spin" />
            )}
            {t("submit")}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
