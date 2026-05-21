"use client";

import { useMemo, useState } from "react";
import {
  IconArrowRight,
  IconLoader2,
  IconRobot,
  IconSparkles,
} from "@tabler/icons-react";
import { useTranslations } from "next-intl";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { useAgentTerm } from "@/components/nav/AgentTermLabel";
import { useCreateAgent } from "@/hooks/use-agent-mutations";
import { useOnboardingStore } from "@/stores/onboarding-store";
import { cn } from "@/lib/utils";

type Choice = "template" | "general" | "blank" | null;

interface OnboardingStepAgentProps {
  onNext: () => void;
}

interface AgentTemplate {
  key: string;
  prompt: string;
}

const TEMPLATES: AgentTemplate[] = [
  {
    key: "sales",
    prompt:
      "You are a sales assistant. Help the user qualify leads, draft outreach emails, and summarize customer conversations clearly and concisely.",
  },
  {
    key: "writing",
    prompt:
      "You are a writing assistant. Improve clarity, tone, and structure of any text the user shares. Offer concrete edits and rationale.",
  },
  {
    key: "general",
    prompt:
      "You are a helpful general-purpose assistant. Answer questions clearly and concisely; ask for clarification when the request is ambiguous.",
  },
];

export function OnboardingStepAgent({ onNext }: OnboardingStepAgentProps) {
  const t = useTranslations("onboarding.agent");
  const tTemplates = useTranslations("onboarding.agent.templates");
  const setDraft = useOnboardingStore((s) => s.setDraft);
  const create = useCreateAgent();
  const term = useAgentTerm();

  const [choice, setChoice] = useState<Choice>(null);
  const [templateKey, setTemplateKey] = useState<string>("general");
  const [name, setName] = useState("");

  const templateLabels: Record<string, string> = useMemo(
    () => ({
      sales: tTemplates("sales"),
      writing: tTemplates("writing"),
      general: tTemplates("general"),
    }),
    [tTemplates],
  );

  const submit = async () => {
    if (!choice) {
      toast.error(t("chooseRequired"));
      return;
    }
    const trimmedName = name.trim();
    let payload: { name: string; persona_md?: string | null };
    if (choice === "template") {
      const tpl = TEMPLATES.find((entry) => entry.key === templateKey);
      payload = {
        name: trimmedName || templateLabels[templateKey] || term,
        persona_md: tpl?.prompt ?? null,
      };
    } else if (choice === "general") {
      const tpl = TEMPLATES.find((entry) => entry.key === "general");
      payload = {
        name: trimmedName || tTemplates("general"),
        persona_md: tpl?.prompt ?? null,
      };
    } else {
      if (!trimmedName) {
        toast.error(t("nameRequired"));
        return;
      }
      payload = { name: trimmedName, persona_md: null };
    }
    try {
      const created = await create.mutateAsync(payload);
      setDraft({ agentId: created.id });
      onNext();
    } catch {
      toast.error(t("createFailed"));
    }
  };

  return (
    <div className="flex flex-col gap-5 px-6 py-6">
      <div className="space-y-1">
        <h2 className="text-xl font-semibold">{t("title", { term })}</h2>
        <p className="text-sm sh-muted">{t("subtitle")}</p>
      </div>

      <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
        <ChoiceCard
          icon={<IconSparkles className="size-4" />}
          title={t("templateTitle")}
          description={t("templateDescription")}
          active={choice === "template"}
          onClick={() => setChoice("template")}
        />
        <ChoiceCard
          icon={<IconRobot className="size-4" />}
          title={t("generalTitle")}
          description={t("generalDescription")}
          active={choice === "general"}
          onClick={() => setChoice("general")}
        />
        <ChoiceCard
          icon={<IconRobot className="size-4" />}
          title={t("blankTitle")}
          description={t("blankDescription")}
          active={choice === "blank"}
          onClick={() => setChoice("blank")}
        />
      </div>

      {choice === "template" && (
        <div className="space-y-1.5">
          <Label>{t("pickTemplate")}</Label>
          <div className="flex flex-wrap gap-2">
            {TEMPLATES.map((tpl) => (
              <button
                key={tpl.key}
                type="button"
                onClick={() => setTemplateKey(tpl.key)}
                className={cn(
                  "rounded-md border px-3 py-1.5 text-[12px]",
                  templateKey === tpl.key
                    ? "border-[rgb(var(--color-primary))] bg-[rgb(var(--color-primary)/0.08)]"
                    : "hover:bg-black/5 dark:hover:bg-white/5",
                )}
              >
                {templateLabels[tpl.key]}
              </button>
            ))}
          </div>
        </div>
      )}

      {choice && (
        <div className="space-y-1.5">
          <Label htmlFor="onboarding-agent-name">{t("nameLabel")}</Label>
          <Input
            id="onboarding-agent-name"
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder={t("namePlaceholder")}
          />
        </div>
      )}

      <div className="flex flex-wrap items-center justify-between gap-2">
        <button
          type="button"
          onClick={onNext}
          className="text-[11px] sh-muted hover:underline"
        >
          {t("skip")}
        </button>
        <Button onClick={submit} disabled={create.isPending || !choice}>
          {create.isPending && <IconLoader2 className="size-4 animate-spin" />}
          {t("create")}
          <IconArrowRight className="size-4" />
        </Button>
      </div>
    </div>
  );
}

function ChoiceCard({
  icon,
  title,
  description,
  active,
  onClick,
}: {
  icon: React.ReactNode;
  title: string;
  description: string;
  active: boolean;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={cn(
        "flex flex-col items-start gap-2 rounded-md border p-3 text-left transition-colors",
        active
          ? "border-[rgb(var(--color-primary))] bg-[rgb(var(--color-primary)/0.05)]"
          : "sh-card hover:bg-black/5 dark:hover:bg-white/5",
      )}
    >
      <span className="flex size-7 items-center justify-center rounded-md bg-[rgb(var(--color-primary)/0.12)] text-[rgb(var(--color-primary))]">
        {icon}
      </span>
      <span className="text-[13px] font-semibold">{title}</span>
      <span className="text-[11px] sh-muted">{description}</span>
    </button>
  );
}
