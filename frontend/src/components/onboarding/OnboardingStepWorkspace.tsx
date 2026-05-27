"use client";

import { useState } from "react";
import { IconLoader2 } from "@tabler/icons-react";
import { useTranslations } from "next-intl";
import { useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { useMe } from "@/hooks/use-me";
import {
  useActiveWorkspace,
  useUpdateWorkspace,
  type WorkspaceRead,
} from "@/hooks/use-workspace";
import { api } from "@/lib/api";
import { slugifyWorkspaceName, switchActiveWorkspace } from "@/lib/workspace";
import { useWorkspaceStore } from "@/stores/workspace-store";
import { useOnboardingStore } from "@/stores/onboarding-store";

interface OnboardingStepWorkspaceProps {
  onNext: () => void;
}

export function OnboardingStepWorkspace({ onNext }: OnboardingStepWorkspaceProps) {
  const t = useTranslations("onboarding.workspace");
  const draft = useOnboardingStore((s) => s.draft);
  const setDraft = useOnboardingStore((s) => s.setDraft);
  const { data: me } = useMe();
  const { data: workspace } = useActiveWorkspace();
  const update = useUpdateWorkspace();
  const activeId = useWorkspaceStore((s) => s.activeWorkspaceId);
  const qc = useQueryClient();
  const [creating, setCreating] = useState(false);

  const [nameInput, setNameInput] = useState<string | null>(null);
  const [descriptionInput, setDescriptionInput] = useState<string | null>(null);
  const name =
    nameInput ?? draft.workspaceName ?? workspace?.name ?? me?.name ?? "";
  const description =
    descriptionInput ?? draft.workspaceDescription ?? workspace?.description ?? "";
  const setName = (value: string) => setNameInput(value);
  const setDescription = (value: string) => setDescriptionInput(value);

  const persistDraft = (trimmedName: string, trimmedDescription: string) => {
    setDraft({
      workspaceName: trimmedName,
      workspaceDescription: trimmedDescription || undefined,
    });
  };

  const submit = async () => {
    const trimmedName = name.trim();
    const trimmedDescription = description.trim();
    if (!trimmedName) {
      toast.error(t("nameRequired"));
      return;
    }
    try {
      if (activeId) {
        await update.mutateAsync({
          name: trimmedName,
          description: trimmedDescription || undefined,
        });
      } else {
        // Platform admin / first-run path: no membership exists yet, so
        // PATCH /workspaces/null would 422. Create the workspace first,
        // then switch the token onto it so the rest of onboarding
        // (provider + agent steps) operates inside the new tenant.
        setCreating(true);
        try {
          const slug = slugifyWorkspaceName(trimmedName) || "workspace";
          const created = await api.post<WorkspaceRead>(
            "/api/v1/workspaces",
            {
              name: trimmedName,
              slug,
              description: trimmedDescription || null,
            },
          );
          const switched = await switchActiveWorkspace(created.id);
          if (!switched) {
            toast.error(t("saveFailed"));
            return;
          }
          await Promise.all([
            qc.invalidateQueries({ queryKey: ["me"] }),
            qc.invalidateQueries({ queryKey: ["workspace"] }),
          ]);
        } finally {
          setCreating(false);
        }
      }
      persistDraft(trimmedName, trimmedDescription);
      onNext();
    } catch {
      toast.error(t("saveFailed"));
    }
  };

  const pending = update.isPending || creating;

  return (
    <div className="flex flex-col gap-5 px-6 py-6">
      <div className="space-y-1">
        <h2 className="text-xl font-semibold">{t("title")}</h2>
        <p className="text-sm sh-muted">{t("subtitle")}</p>
      </div>
      <div className="space-y-3">
        <div className="space-y-1.5">
          <Label htmlFor="onboarding-workspace-name">{t("nameLabel")}</Label>
          <Input
            id="onboarding-workspace-name"
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder={t("namePlaceholder")}
            autoFocus
          />
        </div>
        <div className="space-y-1.5">
          <Label htmlFor="onboarding-workspace-description">
            {t("descriptionLabel")}
          </Label>
          <Textarea
            id="onboarding-workspace-description"
            rows={3}
            value={description}
            onChange={(e) => setDescription(e.target.value)}
            placeholder={t("descriptionPlaceholder")}
          />
        </div>
      </div>
      <div className="flex justify-end">
        <Button onClick={submit} disabled={pending || !name.trim()}>
          {pending && <IconLoader2 className="size-4 animate-spin" />}
          {t("next")}
        </Button>
      </div>
    </div>
  );
}
