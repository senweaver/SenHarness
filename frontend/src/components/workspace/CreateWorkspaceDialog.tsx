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
import { Textarea } from "@/components/ui/textarea";
import { ApiError, api } from "@/lib/api";
import type { WorkspaceRead } from "@/hooks/use-workspace";
import { useWorkspaceQuota } from "@/hooks/use-workspace-quota";
import { switchActiveWorkspace } from "@/lib/workspace";

interface CreateWorkspaceDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
}

function slugify(name: string): string {
  return name
    .toLowerCase()
    .normalize("NFKD")
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "")
    .slice(0, 60);
}

export function CreateWorkspaceDialog({
  open,
  onOpenChange,
}: CreateWorkspaceDialogProps) {
  const t = useTranslations("workspaceSwitcher.createDialog");
  const tCommon = useTranslations("common");
  const tQuota = useTranslations("workspaceQuota");
  const queryClient = useQueryClient();
  const { data: quota } = useWorkspaceQuota();

  const [name, setName] = useState("");
  const [slugTouched, setSlugTouched] = useState(false);
  const [slugInput, setSlugInput] = useState("");
  const [description, setDescription] = useState("");
  const slug = slugTouched ? slugInput : slugify(name);

  const handleOpenChange = (next: boolean) => {
    if (!next) {
      setName("");
      setSlugInput("");
      setSlugTouched(false);
      setDescription("");
    }
    onOpenChange(next);
  };

  const create = useMutation<WorkspaceRead, unknown, void>({
    mutationFn: () =>
      api.post<WorkspaceRead>("/api/v1/workspaces", {
        name: name.trim(),
        slug: slug.trim(),
        description: description.trim() || null,
      }),
    onSuccess: async (created) => {
      toast.success(t("created"));
      queryClient.invalidateQueries({ queryKey: ["me"] });
      handleOpenChange(false);
      const switched = await switchActiveWorkspace(created.id);
      if (switched && typeof window !== "undefined") {
        window.location.reload();
      }
    },
    onError: (err) => {
      if (err instanceof ApiError) {
        toast.error(err.message);
      } else {
        toast.error(t("createFailed"));
      }
    },
  });

  const cannotCreate = Boolean(
    quota && (!quota.creation_kind_allowed || quota.remaining <= 0),
  );
  const blockReason =
    quota && cannotCreate
      ? !quota.creation_kind_allowed
        ? tQuota("blocked.notPermitted")
        : tQuota("blocked.quotaReached", {
            used: quota.used,
            limit: quota.limit,
          })
      : null;

  const valid =
    name.trim().length > 0 && /^[a-z0-9][a-z0-9-]{1,63}$/.test(slug.trim());

  return (
    <Dialog open={open} onOpenChange={handleOpenChange}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>{t("title")}</DialogTitle>
          <DialogDescription>{t("description")}</DialogDescription>
        </DialogHeader>

        {blockReason && (
          <p className="rounded-md border border-dashed p-3 text-[12px] sh-muted">
            {blockReason}
          </p>
        )}

        <div className="space-y-3">
          <div className="space-y-1.5">
            <Label htmlFor="workspace-create-name">{t("nameLabel")}</Label>
            <Input
              id="workspace-create-name"
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder={t("namePlaceholder")}
              autoFocus
            />
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="workspace-create-slug">{t("slugLabel")}</Label>
            <Input
              id="workspace-create-slug"
              value={slug}
              onChange={(e) => {
                setSlugTouched(true);
                setSlugInput(e.target.value);
              }}
              placeholder="my-team"
            />
            <p className="text-[11px] sh-muted">{t("slugHint")}</p>
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="workspace-create-description">
              {t("descriptionLabel")}
            </Label>
            <Textarea
              id="workspace-create-description"
              value={description}
              onChange={(e) => setDescription(e.target.value)}
              rows={2}
              placeholder={t("descriptionPlaceholder")}
            />
          </div>
        </div>

        <DialogFooter>
          <Button variant="ghost" onClick={() => handleOpenChange(false)}>
            {tCommon("cancel")}
          </Button>
          <Button
            onClick={() => create.mutate()}
            disabled={!valid || create.isPending || cannotCreate}
          >
            {create.isPending && (
              <IconLoader2 className="size-4 animate-spin" />
            )}
            {t("submit")}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
