"use client";

import { useEffect, useMemo, useState } from "react";
import { Link } from "@/lib/navigation";
import {
  IconChevronRight,
  IconPlus,
  IconSearch,
  IconSparkles,
} from "@tabler/icons-react";
import { AgentAvatar } from "@/components/agents/AgentAvatar";
import { useLocale, useTranslations } from "next-intl";

import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import {
  useDiscoverAgents,
  useDiscoverCategories,
} from "@/hooks/use-marketplace";
import type { AgentPublicCard } from "@/types/api";

interface NewAgentDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /**
   * Open the editable blank-agent dialog. ``initial`` carries the
   * prefill the caller wants the editor to show — the user can change
   * the name (and any other field) before clicking Save. When
   * ``initial.templateId`` is set, Save calls clone instead of create.
   */
  onPickBlank: (initial?: {
    name?: string;
    description?: string;
    defaultModel?: string | null;
    templateId?: string;
  }) => void;
}

export function NewAgentDialog({
  open,
  onOpenChange,
  onPickBlank,
}: NewAgentDialogProps) {
  const t = useTranslations("newAgent");
  const tCommon = useTranslations("common");
  const locale = useLocale();

  const [q, setQ] = useState("");
  const [debouncedQ, setDebouncedQ] = useState("");
  const [selectedCategory, setSelectedCategory] = useState<string | null>(null);

  useEffect(() => {
    const id = setTimeout(() => setDebouncedQ(q), 300);
    return () => clearTimeout(id);
  }, [q]);

  const { data: rawTemplates } = useDiscoverAgents({
    q: debouncedQ,
    category: selectedCategory,
    templateOnly: true,
    limit: 200,
  });
  const { data: categories } = useDiscoverCategories({ templateOnly: true });

  const templates = useMemo(() => rawTemplates ?? [], [rawTemplates]);

  const totalCount = useMemo(
    () => categories?.reduce((acc, c) => acc + c.count, 0) ?? 0,
    [categories],
  );

  // The server already applies the category filter; this keeps the grouped
  // sections in sync if a stale fetch briefly contains other categories.
  const filteredTemplates = useMemo(() => {
    if (!selectedCategory) return templates;
    return templates.filter((card) => card.category === selectedCategory);
  }, [templates, selectedCategory]);

  const sections = useMemo(() => {
    const order = (categories ?? []).map((c) => c.slug);
    const groups = new Map<string, AgentPublicCard[]>();
    for (const card of filteredTemplates) {
      if (!card.category) continue;
      const list = groups.get(card.category) ?? [];
      list.push(card);
      groups.set(card.category, list);
    }
    return order
      .filter((slug) => groups.has(slug))
      .map((slug) => {
        const cat = categories?.find((c) => c.slug === slug);
        const label = cat
          ? locale === "zh-CN"
            ? cat.name_cn
            : cat.name_en
          : slug;
        return { slug, label, items: groups.get(slug) ?? [] };
      });
  }, [filteredTemplates, categories, locale]);

  const cloneTemplate = (id: string, defaultName: string, description: string | null) => {
    onOpenChange(false);
    onPickBlank({
      name: defaultName,
      description: description ?? "",
      templateId: id,
    });
  };

  const createGeneral = () => {
    onOpenChange(false);
    onPickBlank({ name: t("general.defaultName") });
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="flex h-[min(720px,85vh)] flex-col gap-0 p-0 sm:max-w-5xl">
        <header className="flex flex-shrink-0 items-center gap-3 px-5 py-4 pr-12">
          <DialogTitle className="flex-1 text-base font-semibold leading-none">
            {t("title")}
          </DialogTitle>
          <div className="relative w-[240px]">
            <IconSearch className="pointer-events-none absolute left-2 top-1/2 size-3.5 -translate-y-1/2 sh-muted" />
            <Input
              value={q}
              onChange={(e) => setQ(e.target.value)}
              placeholder={t("searchPlaceholder")}
              className="pl-7"
            />
          </div>
        </header>

        <div className="grid min-h-0 flex-1 grid-cols-1 border-t lg:grid-cols-[180px_1fr]">
          <aside className="space-y-1 overflow-y-auto border-b p-3 lg:border-b-0 lg:border-r">
            <button
              type="button"
              onClick={() => setSelectedCategory(null)}
              className={`flex w-full items-center justify-between rounded-md px-2 py-1.5 text-left text-sm hover:bg-black/5 dark:hover:bg-white/5 ${
                selectedCategory === null
                  ? "bg-[rgb(var(--color-primary)/0.08)] font-medium text-[rgb(var(--color-primary))]"
                  : ""
              }`}
            >
              <span>{t("allCategories")}</span>
              <span className="text-xs sh-muted tabular-nums">{totalCount}</span>
            </button>
            {(categories ?? []).map((c) => (
              <button
                key={c.slug}
                type="button"
                onClick={() => setSelectedCategory(c.slug)}
                className={`flex w-full items-center justify-between rounded-md px-2 py-1.5 text-left text-sm hover:bg-black/5 dark:hover:bg-white/5 ${
                  selectedCategory === c.slug
                    ? "bg-[rgb(var(--color-primary)/0.08)] font-medium text-[rgb(var(--color-primary))]"
                    : ""
                }`}
              >
                <span className="truncate">
                  {locale === "zh-CN" ? c.name_cn : c.name_en}
                </span>
                <span className="text-xs sh-muted tabular-nums">{c.count}</span>
              </button>
            ))}
          </aside>

          <div className="space-y-6 overflow-y-auto p-5">
            <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
              <button
                type="button"
                onClick={() => onPickBlank()}
                data-testid="new-agent-blank-tile"
                className="group flex w-full items-center gap-3 rounded-md border border-dashed p-4 text-left transition-all hover:-translate-y-px hover:border-[rgb(var(--color-primary))] hover:bg-[rgb(var(--color-primary)/0.06)]"
              >
                <div className="flex size-10 shrink-0 items-center justify-center rounded-md bg-[rgb(var(--color-primary)/0.12)] text-[rgb(var(--color-primary))]">
                  <IconPlus className="size-5" />
                </div>
                <div className="min-w-0 flex-1">
                  <p className="truncate text-[13px] font-semibold">
                    {t("custom.title")}
                  </p>
                  <p className="line-clamp-2 text-[12px] sh-muted">
                    {t("custom.subtitle")}
                  </p>
                </div>
                <IconChevronRight className="size-4 shrink-0 sh-muted" />
              </button>

              <button
                type="button"
                onClick={createGeneral}
                className="group flex w-full items-center gap-3 rounded-md border p-4 text-left transition-all hover:-translate-y-px hover:border-[rgb(var(--color-primary))] hover:bg-[rgb(var(--color-primary)/0.06)]"
              >
                <div className="flex size-10 shrink-0 items-center justify-center rounded-md bg-[rgb(var(--color-primary)/0.12)] text-[rgb(var(--color-primary))]">
                  <IconSparkles className="size-5" />
                </div>
                <div className="min-w-0 flex-1">
                  <p className="truncate text-[13px] font-semibold">
                    {t("general.title")}
                  </p>
                  <p className="truncate text-[12px] sh-muted">
                    {t("general.subtitle")}
                  </p>
                </div>
                <IconChevronRight className="size-4 shrink-0 sh-muted" />
              </button>
            </div>

            {sections.length === 0 ? (
              <p className="rounded-md border border-dashed p-8 text-center text-sm sh-muted">
                {t("templatesEmpty")}
              </p>
            ) : (
              sections.map((section) => (
                <section key={section.slug} className="space-y-2">
                  <h3 className="text-[11px] font-semibold uppercase tracking-wide sh-muted">
                    {section.label}{" "}
                    <span className="tabular-nums">({section.items.length})</span>
                  </h3>
                  <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3">
                    {section.items.map((agent) => (
                      <TemplateCard
                        key={agent.id}
                        agent={agent}
                        onPick={() =>
                          cloneTemplate(agent.id, agent.name, agent.description ?? null)
                        }
                      />
                    ))}
                  </div>
                </section>
              ))
            )}
          </div>
        </div>

        <footer className="flex flex-shrink-0 items-center justify-between border-t px-5 py-3">
          <Link
            href="/marketplace"
            className="text-[12px] font-medium text-[rgb(var(--color-primary))] hover:underline"
            onClick={() => onOpenChange(false)}
          >
            {t("browseMarketplace")}
          </Link>
          <Button variant="ghost" onClick={() => onOpenChange(false)}>
            {tCommon("cancel")}
          </Button>
        </footer>
      </DialogContent>
    </Dialog>
  );
}

function TemplateCard({
  agent,
  onPick,
}: {
  agent: AgentPublicCard;
  onPick: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onPick}
      className="group relative flex h-full flex-col items-start gap-2 rounded-md border sh-card p-3 text-left transition-all hover:-translate-y-px hover:border-[rgb(var(--color-primary))] hover:bg-[rgb(var(--color-primary)/0.06)]"
    >
      <IconChevronRight className="absolute right-2 top-2 size-4 shrink-0 sh-muted" />
      <div className="flex w-full items-center gap-2 pr-5">
        <AgentAvatar
          name={agent.name}
          avatarUrl={agent.avatar_url}
          className="size-8 rounded-md"
          fallbackClassName="rounded-md"
        />
        <span className="truncate text-[13px] font-semibold">{agent.name}</span>
      </div>
      {agent.description && (
        <p className="line-clamp-2 text-[12px] sh-muted">{agent.description}</p>
      )}
    </button>
  );
}
