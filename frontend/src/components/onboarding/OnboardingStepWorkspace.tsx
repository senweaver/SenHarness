"use client";

import { useState } from "react";
import { IconLoader2 } from "@tabler/icons-react";
import { useTranslations } from "next-intl";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { useMe } from "@/hooks/use-me";
import { useActiveWorkspace, useUpdateWorkspace } from "@/hooks/use-workspace";
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

  const [nameInput, setNameInput] = useState<string | null>(null);
  const [descriptionInput, setDescriptionInput] = useState<string | null>(null);
  const name =
    nameInput ?? draft.workspaceName ?? workspace?.name ?? me?.name ?? "";
  const description =
    descriptionInput ?? draft.workspaceDescription ?? workspace?.description ?? "";
  const setName = (value: string) => setNameInput(value);
  const setDescription = (value: string) => setDescriptionInput(value);

  const submit = async () => {
    if (!name.trim()) {
      toast.error(t("nameRequired"));
      return;
    }
    try {
      await update.mutateAsync({
        name: name.trim(),
        description: description.trim() || undefined,
      });
      setDraft({
        workspaceName: name.trim(),
        workspaceDescription: description.trim() || undefined,
      });
      onNext();
    } catch {
      toast.error(t("saveFailed"));
    }
  };

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
        <Button onClick={submit} disabled={update.isPending || !name.trim()}>
          {update.isPending && <IconLoader2 className="size-4 animate-spin" />}
          {t("next")}
        </Button>
      </div>
    </div>
  );
}
