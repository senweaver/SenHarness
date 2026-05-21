"use client";

import { useEffect, useMemo, useState } from "react";
import { Link } from "@/lib/navigation";
import { useRouter } from "@/lib/navigation";
import {
  IconCopy,
  IconDotsVertical,
  IconFlag,
  IconMessagePlus,
  IconRobot,
  IconSearch,
  IconSparkles,
  IconStarFilled,
  IconX,
} from "@tabler/icons-react";
import { useLocale, useTranslations } from "next-intl";
import { toast } from "sonner";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { PageHeader } from "@/components/ui/page-header";
import { Skeleton } from "@/components/ui/skeleton";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { ReportDialog } from "@/components/marketplace/ReportDialog";
import {
  useCloneAgent,
  useDiscoverAgents,
  useDiscoverCategories,
} from "@/hooks/use-marketplace";
import { relativeTime } from "@/lib/utils";
import type { AgentPublicCard } from "@/types/api";

export default function MarketplacePage() {
  const t = useTranslations("marketplace");
  const tAgents = useTranslations("settings.agents");
  const locale = useLocale();
  const router = useRouter();

  const [q, setQ] = useState("");
  const [debouncedQ, setDebouncedQ] = useState("");
  const [category, setCategory] = useState<string | null>(null);
  const [tag, setTag] = useState<string | null>(null);
  const [templateOnly, setTemplateOnly] = useState(false);

  // 300ms debounce so we don't refetch on every keystroke.
  useEffect(() => {
    const id = setTimeout(() => setDebouncedQ(q), 300);
    return () => clearTimeout(id);
  }, [q]);

  const { data, isLoading } = useDiscoverAgents({
    q: debouncedQ,
    category,
    tag,
    templateOnly,
  });
  const { data: categories } = useDiscoverCategories();
  const clone = useCloneAgent();

  const sorted = useMemo(() => data ?? [], [data]);

  const totalCount = useMemo(
    () => categories?.reduce((acc, c) => acc + c.count, 0) ?? 0,
    [categories],
  );

  const onClone = async (a: AgentPublicCard) => {
    try {
      const copy = await clone.mutateAsync({ agent_id: a.id });
      toast.success(t("cloned", { name: copy.name }));
      router.push(`/agents/${copy.id}/edit`);
    } catch {
      toast.error(t("cloneFailed"));
    }
  };

  const categoryLabel = (slug: string | null): string => {
    if (!slug) return t("filters.allCategories");
    const c = categories?.find((x) => x.slug === slug);
    if (!c) return slug;
    return locale === "zh-CN" ? c.name_cn : c.name_en;
  };

  return (
    <div className="p-6">
      <PageHeader
        title={t("title")}
        description={t("description")}
        actions={
          <div className="flex items-center gap-2">
            <Button
              size="sm"
              variant={templateOnly ? "default" : "outline"}
              onClick={() => setTemplateOnly((v) => !v)}
              title={t("filters.builtinHint")}
            >
              {t("filters.builtin")}
            </Button>
            <div className="relative w-[260px]">
              <IconSearch className="pointer-events-none absolute left-2 top-1/2 size-3.5 -translate-y-1/2 sh-muted" />
              <Input
                value={q}
                onChange={(e) => setQ(e.target.value)}
                placeholder={t("searchPlaceholder")}
                className="pl-7"
              />
            </div>
          </div>
        }
      />

      <div className="mb-4 grid grid-cols-1 gap-4 lg:grid-cols-[200px_1fr]">
        <aside className="space-y-1">
          <button
            type="button"
            onClick={() => {
              setCategory(null);
              setTag(null);
            }}
            className={`flex w-full items-center justify-between rounded-md px-2 py-1.5 text-left text-sm hover:bg-black/5 dark:hover:bg-white/5 ${
              category === null ? "bg-black/5 font-medium dark:bg-white/5" : ""
            }`}
          >
            <span>{t("filters.allCategories")}</span>
            <span className="text-xs sh-muted tabular-nums">{totalCount}</span>
          </button>
          {(categories ?? []).map((c) => (
            <button
              key={c.slug}
              type="button"
              onClick={() => {
                setCategory(c.slug);
                setTag(null);
              }}
              className={`flex w-full items-center justify-between rounded-md px-2 py-1.5 text-left text-sm hover:bg-black/5 dark:hover:bg-white/5 ${
                category === c.slug
                  ? "bg-black/5 font-medium dark:bg-white/5"
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

        <div>
          {(category || tag) && (
            <div className="mb-3 flex flex-wrap items-center gap-1.5 text-xs">
              <span className="sh-muted">{t("filters.activeLabel")}:</span>
              {category && (
                <Badge
                  variant="default"
                  className="cursor-pointer gap-1"
                  onClick={() => setCategory(null)}
                >
                  {categoryLabel(category)}
                  <IconX className="size-3" />
                </Badge>
              )}
              {tag && (
                <Badge
                  variant="default"
                  className="cursor-pointer gap-1"
                  onClick={() => setTag(null)}
                >
                  #{tag}
                  <IconX className="size-3" />
                </Badge>
              )}
            </div>
          )}

          {isLoading && (
            <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-2 xl:grid-cols-3">
              {[0, 1, 2, 3].map((i) => (
                <Skeleton key={i} className="h-40" />
              ))}
            </div>
          )}

          {!isLoading && sorted.length === 0 && (
            <Card>
              <CardContent className="flex flex-col items-center gap-2 py-10 text-center">
                <IconSparkles className="size-8 sh-muted" />
                <p className="text-sm sh-muted">
                  {debouncedQ
                    ? t("emptyForQuery", { q: debouncedQ })
                    : t("empty")}
                </p>
              </CardContent>
            </Card>
          )}

          <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-2 xl:grid-cols-3">
            {sorted.map((a) => {
              const meta = (a.metadata_json ?? {}) as Record<string, unknown>;
              const codeMode = Boolean(meta.code_mode);
              return (
                <Card key={a.id} className="flex flex-col">
                  <CardHeader className="flex-1">
                    <div className="flex items-center gap-2">
                      {a.avatar_url ? (
                        <img
                          src={a.avatar_url}
                          alt=""
                          className="size-9 shrink-0 rounded-full object-cover"
                        />
                      ) : (
                        <div className="flex size-9 shrink-0 items-center justify-center rounded-full bg-black/10 dark:bg-white/10">
                          <IconRobot className="size-5" />
                        </div>
                      )}
                      <div className="min-w-0 flex-1">
                        <CardTitle className="truncate">
                          <Link
                            href={`/agents/${a.id}`}
                            className="hover:underline"
                          >
                            {a.name}
                          </Link>
                        </CardTitle>
                        <div className="flex items-center gap-2 text-[11px] sh-muted">
                          <span
                            className="inline-flex items-center gap-0.5 tabular-nums"
                            title={t("starsHint")}
                          >
                            <IconStarFilled className="size-3 text-yellow-500" />
                            {a.stars}
                          </span>
                          <span>·</span>
                          <span>{relativeTime(a.updated_at, locale)}</span>
                        </div>
                      </div>
                    </div>
                    {a.description && (
                      <CardDescription className="line-clamp-3">
                        {a.description}
                      </CardDescription>
                    )}
                  </CardHeader>
                  <CardContent className="space-y-2">
                    <div className="flex flex-wrap gap-1.5">
                      <Badge variant="outline">{a.backend_kind}</Badge>
                      <Badge variant="default">
                        {a.autonomy_level.toUpperCase()}
                      </Badge>
                      {codeMode && <Badge variant="primary">CodeMode</Badge>}
                      {(a.tags ?? []).slice(0, 4).map((tg) => (
                        <Badge
                          key={tg}
                          variant="outline"
                          className="cursor-pointer"
                          onClick={() => setTag(tg)}
                          title={t("filters.filterByTagHint", { tag: tg })}
                        >
                          #{tg}
                        </Badge>
                      ))}
                    </div>
                    <div className="flex items-center gap-1.5">
                      <Button
                        size="sm"
                        variant="outline"
                        onClick={() => onClone(a)}
                        disabled={clone.isPending}
                        className="flex-1"
                        title={t("clone")}
                      >
                        <IconCopy className="size-3.5" />
                        {t("clone")}
                      </Button>
                      <Button asChild size="sm" className="flex-1">
                        <Link href={`/chat/new?agent=${a.id}`}>
                          <IconMessagePlus className="size-3.5" />
                          {tAgents("detail.startChat")}
                        </Link>
                      </Button>
                      <DropdownMenu>
                        <DropdownMenuTrigger asChild>
                          <Button
                            size="icon"
                            variant="ghost"
                            className="size-7 shrink-0"
                            aria-label={t("more")}
                          >
                            <IconDotsVertical className="size-3.5" />
                          </Button>
                        </DropdownMenuTrigger>
                        <DropdownMenuContent align="end">
                          <ReportDialog
                            agentId={a.id}
                            trigger={
                              <DropdownMenuItem
                                onSelect={(e) => e.preventDefault()}
                                className="text-amber-600"
                              >
                                <IconFlag className="size-3.5" />
                                {t("report.trigger")}
                              </DropdownMenuItem>
                            }
                          />
                        </DropdownMenuContent>
                      </DropdownMenu>
                    </div>
                  </CardContent>
                </Card>
              );
            })}
          </div>
        </div>
      </div>
    </div>
  );
}

