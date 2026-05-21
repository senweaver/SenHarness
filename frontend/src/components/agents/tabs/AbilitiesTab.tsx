"use client";

import { useEffect, useMemo, useState } from "react";
import { Link } from "@/lib/navigation";
import { IconPlus, IconX } from "@tabler/icons-react";
import { useTranslations } from "next-intl";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { Label } from "@/components/ui/label";
import { Switch } from "@/components/ui/switch";
import { Textarea } from "@/components/ui/textarea";
import { useSkills } from "@/hooks/use-skills";
import { useUpdateAgent } from "@/hooks/use-agent-mutations";
import { useToolRegistry, type ToolCategory } from "@/hooks/use-tools";
import { SkillAttachDialog } from "@/components/agents/dialogs/SkillAttachDialog";
import { cn } from "@/lib/utils";
import type { AgentRead } from "@/types/api";

const RECOMMENDED_QUESTIONS_MAX = 6;

const TOOL_CATEGORY_ORDER: ToolCategory[] = [
  "utility",
  "web",
  "filesystem",
  "memory",
  "multimedia",
  "coding",
];

interface AbilitiesTabProps {
  agent: AgentRead;
}

function readAttachedSlugs(agent: AgentRead): string[] {
  const meta = (agent.metadata_json ?? {}) as { skills?: unknown };
  if (!Array.isArray(meta.skills)) return [];
  return meta.skills
    .map((s) => (typeof s === "string" ? s : null))
    .filter((s): s is string => Boolean(s));
}

function readBuiltinTools(agent: AgentRead): string[] | null {
  const meta = (agent.metadata_json ?? {}) as {
    tools?: { builtin?: unknown };
  };
  const builtin = meta.tools?.builtin;
  if (!Array.isArray(builtin)) return null;
  return builtin
    .map((t) => (typeof t === "string" ? t : null))
    .filter((t): t is string => Boolean(t));
}

function readWelcomeMessage(agent: AgentRead): string {
  const meta = (agent.metadata_json ?? {}) as { welcome_message?: unknown };
  return typeof meta.welcome_message === "string" ? meta.welcome_message : "";
}

function readRecommendedQuestions(agent: AgentRead): string[] {
  const meta = (agent.metadata_json ?? {}) as {
    recommended_questions?: unknown;
  };
  if (!Array.isArray(meta.recommended_questions)) return [];
  return meta.recommended_questions
    .map((q) => (typeof q === "string" ? q : null))
    .filter((q): q is string => Boolean(q && q.trim()))
    .slice(0, RECOMMENDED_QUESTIONS_MAX);
}

function readSuggestionsEnabled(agent: AgentRead): boolean {
  const meta = (agent.metadata_json ?? {}) as {
    chat_features?: { suggestions_enabled?: unknown };
  };
  return Boolean(meta.chat_features?.suggestions_enabled);
}

export function AbilitiesTab({ agent }: AbilitiesTabProps) {
  const t = useTranslations("agentDetail.abilities");
  const tTools = useTranslations("agentDetail.abilities.tools");
  const tChat = useTranslations("agentDetail.abilities.chat");
  const { data: skills } = useSkills();
  const { data: toolRegistry } = useToolRegistry();
  const [attachOpen, setAttachOpen] = useState(false);
  const update = useUpdateAgent(agent.id);

  const [welcome, setWelcome] = useState(readWelcomeMessage(agent));
  const [questionsText, setQuestionsText] = useState(() =>
    readRecommendedQuestions(agent).join("\n"),
  );
  useEffect(() => {
    setWelcome(readWelcomeMessage(agent));
    setQuestionsText(readRecommendedQuestions(agent).join("\n"));
  }, [agent.id, agent.metadata_json]);

  const suggestionsEnabled = readSuggestionsEnabled(agent);

  const attachedSlugs = useMemo(() => readAttachedSlugs(agent), [agent]);
  const attached = useMemo(() => {
    const lookup = new Map((skills ?? []).map((s) => [s.slug, s] as const));
    return attachedSlugs.map((slug) => ({
      slug,
      skill: lookup.get(slug) ?? null,
    }));
  }, [skills, attachedSlugs]);

  const builtinTools = useMemo(() => readBuiltinTools(agent), [agent]);
  const overrideActive = builtinTools !== null;
  const enabledSet = useMemo(
    () => new Set(builtinTools ?? []),
    [builtinTools],
  );
  const groupedTools = useMemo(() => {
    const buckets = new Map<ToolCategory, typeof toolRegistry>();
    for (const row of toolRegistry ?? []) {
      const arr = (buckets.get(row.category) ?? []) as NonNullable<
        typeof toolRegistry
      >;
      arr.push(row);
      buckets.set(row.category, arr);
    }
    return TOOL_CATEGORY_ORDER.flatMap((cat) => {
      const items = buckets.get(cat);
      if (!items || items.length === 0) return [];
      return [{ category: cat, items: [...items].sort((a, b) => a.name.localeCompare(b.name)) }];
    });
  }, [toolRegistry]);

  const onRemove = async (slug: string) => {
    const next = attachedSlugs.filter((s) => s !== slug);
    const nextMeta = { ...(agent.metadata_json ?? {}), skills: next };
    try {
      await update.mutateAsync({ metadata_json: nextMeta });
    } catch (err) {
      toast.error(t("removeFailed", { error: (err as Error).message }));
    }
  };

  const commitMetadata = async (patch: Record<string, unknown>) => {
    const next = { ...(agent.metadata_json ?? {}), ...patch };
    await update.mutateAsync({ metadata_json: next });
  };

  const onCommitWelcome = async () => {
    const next = welcome.trim();
    const current = readWelcomeMessage(agent);
    if (next === current) return;
    try {
      await commitMetadata({ welcome_message: next ? next : null });
    } catch (err) {
      toast.error(tChat("saveFailed", { error: (err as Error).message }));
      setWelcome(current);
    }
  };

  const onCommitQuestions = async () => {
    const next = questionsText
      .split("\n")
      .map((q) => q.trim())
      .filter(Boolean)
      .slice(0, RECOMMENDED_QUESTIONS_MAX);
    const current = readRecommendedQuestions(agent);
    if (next.length === current.length && next.every((q, i) => q === current[i])) {
      return;
    }
    try {
      await commitMetadata({ recommended_questions: next });
      setQuestionsText(next.join("\n"));
    } catch (err) {
      toast.error(tChat("saveFailed", { error: (err as Error).message }));
      setQuestionsText(current.join("\n"));
    }
  };

  const onToggleSuggestions = async (v: boolean) => {
    const meta = agent.metadata_json ?? {};
    const features =
      (meta.chat_features as Record<string, unknown> | undefined) ?? {};
    try {
      await update.mutateAsync({
        metadata_json: {
          ...meta,
          chat_features: { ...features, suggestions_enabled: v },
        },
      });
    } catch (err) {
      toast.error(tChat("saveFailed", { error: (err as Error).message }));
    }
  };

  const onToggleTool = async (name: string, checked: boolean) => {
    // Build the next set off the *current* override (or an empty list if
    // the agent was running on the default toolbox). Once we write any
    // explicit array we lock in allow-list mode — that's the contract
    // the runner reads via metadata.tools.builtin.
    const base = new Set(builtinTools ?? []);
    if (checked) base.add(name);
    else base.delete(name);
    const next = Array.from(base);
    const meta = agent.metadata_json ?? {};
    const tools = (meta.tools as Record<string, unknown> | undefined) ?? {};
    const nextMeta = {
      ...meta,
      tools: { ...tools, builtin: next },
    };
    try {
      await update.mutateAsync({ metadata_json: nextMeta });
    } catch (err) {
      toast.error(
        tTools("toggleFailed", { error: (err as Error).message }),
      );
    }
  };

  return (
    <div className="space-y-6">
      <header className="flex items-center justify-between">
        <h2 className="text-base font-semibold">{t("title")}</h2>
      </header>

      <section className="rounded-md border sh-card p-5">
        <div className="mb-3 flex items-center justify-between">
          <div>
            <h3 className="text-sm font-semibold">{t("skillsTitle")}</h3>
            <p className="text-[12px] sh-muted">
              {t("attachedCount", { count: attached.length })}
            </p>
          </div>
          <Button size="sm" onClick={() => setAttachOpen(true)}>
            <IconPlus className="size-4" />
            {t("addSkill")}
          </Button>
        </div>
        {attached.length === 0 ? (
          <p className="text-[13px] sh-muted">
            {t.rich("skillsEmptyWithLink", {
              link: (chunks) => (
                <Link
                  href="/skills"
                  className="text-primary underline-offset-2 hover:underline"
                >
                  {chunks}
                </Link>
              ),
            })}
          </p>
        ) : (
          <div className="flex flex-wrap gap-1.5">
            {attached.map(({ slug, skill }) => (
              <span
                key={slug}
                className={cn(
                  "group inline-flex items-center gap-1 rounded-md border px-2 py-0.5 text-xs",
                  skill ? "" : "border-dashed sh-muted",
                )}
                title={skill?.description ?? slug}
              >
                <span>{skill?.name ?? slug}</span>
                <button
                  type="button"
                  onClick={() => onRemove(slug)}
                  disabled={update.isPending}
                  className="opacity-0 transition group-hover:opacity-100 hover:text-destructive disabled:opacity-50"
                  aria-label={t("remove", { name: skill?.name ?? slug })}
                >
                  <IconX className="size-3" />
                </button>
              </span>
            ))}
          </div>
        )}
      </section>

      <section className="rounded-md border sh-card p-5">
        <div className="mb-3 flex items-center justify-between">
          <h3 className="text-sm font-semibold">{t("knowledgeTitle")}</h3>
          <Button asChild size="sm" variant="outline">
            <Link href="/knowledge">
              <IconPlus className="size-4" />
              {t("attachKnowledge")}
            </Link>
          </Button>
        </div>
        <p className="text-[13px] sh-muted">{t("knowledgeEmpty")}</p>
      </section>

      <section className="rounded-md border sh-card p-5">
        <header className="mb-3">
          <h3 className="text-sm font-semibold">{t("toolsTitle")}</h3>
          <p className="text-[12px] sh-muted">
            {overrideActive
              ? tTools("bannerOverride", { count: enabledSet.size })
              : tTools("bannerDefault")}
          </p>
        </header>
        {groupedTools.length === 0 ? (
          <p className="text-[12px] sh-muted">{tTools("empty")}</p>
        ) : (
          <div className="space-y-4">
            {groupedTools.map(({ category, items }) => (
              <div key={category}>
                <div className="mb-1 text-[10px] uppercase tracking-wide sh-muted">
                  {tTools(`category.${category}`)}
                </div>
                <ul className="space-y-1">
                  {items.map((row) => {
                    const enabled = overrideActive
                      ? enabledSet.has(row.name)
                      : row.default_in.includes("default");
                    return (
                      <li
                        key={row.name}
                        className="flex items-start justify-between gap-3 rounded-md border bg-card px-2.5 py-1.5"
                      >
                        <div className="min-w-0">
                          <div className="flex items-center gap-1.5">
                            <span className="font-mono text-xs">{row.name}</span>
                            {row.default_in.includes("default") ? (
                              <span className="rounded-sm bg-muted px-1 py-px text-[10px] sh-muted">
                                {tTools("defaultBadge")}
                              </span>
                            ) : null}
                          </div>
                          <p className="mt-0.5 text-[11px] sh-muted line-clamp-2">
                            {row.description}
                          </p>
                        </div>
                        <Switch
                          checked={enabled}
                          disabled={update.isPending}
                          onCheckedChange={(v) => onToggleTool(row.name, v)}
                          aria-label={row.name}
                        />
                      </li>
                    );
                  })}
                </ul>
              </div>
            ))}
          </div>
        )}
      </section>

      <section className="rounded-md border sh-card p-5">
        <header className="mb-3">
          <h3 className="text-sm font-semibold">{tChat("title")}</h3>
          <p className="text-[12px] sh-muted">{tChat("subtitle")}</p>
        </header>
        <div className="space-y-4">
          <div className="space-y-1.5">
            <Label className="text-[12px]">{tChat("welcomeLabel")}</Label>
            <Textarea
              value={welcome}
              onChange={(e) => setWelcome(e.target.value)}
              onBlur={onCommitWelcome}
              placeholder={tChat("welcomePlaceholder")}
              rows={3}
              className="resize-y text-[13px]"
            />
          </div>
          <div className="space-y-1.5">
            <Label className="text-[12px]">{tChat("questionsLabel")}</Label>
            <Textarea
              value={questionsText}
              onChange={(e) => setQuestionsText(e.target.value)}
              onBlur={onCommitQuestions}
              placeholder={tChat("questionsPlaceholder")}
              rows={4}
              className="resize-y text-[13px]"
            />
            <p className="text-[11px] sh-muted">
              {tChat("questionsHint", { max: RECOMMENDED_QUESTIONS_MAX })}
            </p>
          </div>
          <div className="flex items-start justify-between gap-3">
            <div>
              <Label className="text-[12px]">{tChat("suggestionsLabel")}</Label>
              <p className="text-[11px] sh-muted">
                {tChat("suggestionsHint")}
              </p>
            </div>
            <Switch
              checked={suggestionsEnabled}
              disabled={update.isPending}
              onCheckedChange={onToggleSuggestions}
              aria-label={tChat("suggestionsLabel")}
            />
          </div>
        </div>
      </section>

      <SkillAttachDialog
        agent={agent}
        open={attachOpen}
        onOpenChange={setAttachOpen}
      />
    </div>
  );
}
