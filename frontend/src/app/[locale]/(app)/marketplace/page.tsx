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

  // 300ms debounce so we don't refetch on every keystroke.
  useEffect(() => {
    const id = setTimeout(() => setDebouncedQ(q), 300);
    return () => clearTimeout(id);
  }, [q]);

  const { data, isLoading } = useDiscoverAgents(debouncedQ);
  const clone = useCloneAgent();

  const sorted = useMemo(() => data ?? [], [data]);

  const onClone = async (a: AgentPublicCard) => {
    try {
      const copy = await clone.mutateAsync({ agent_id: a.id });
      toast.success(t("cloned", { name: copy.name }));
      router.push(`/agents/${copy.id}/edit`);
    } catch {
      toast.error(t("cloneFailed"));
    }
  };

  return (
    <div className="p-6">
      <PageHeader
        title={t("title")}
        description={t("description")}
        actions={
          <div className="relative w-[260px]">
            <IconSearch className="pointer-events-none absolute left-2 top-1/2 size-3.5 -translate-y-1/2 sh-muted" />
            <Input
              value={q}
              onChange={(e) => setQ(e.target.value)}
              placeholder={t("searchPlaceholder")}
              className="pl-7"
            />
          </div>
        }
      />

      {isLoading && (
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4">
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
              {debouncedQ ? t("emptyForQuery", { q: debouncedQ }) : t("empty")}
            </p>
          </CardContent>
        </Card>
      )}

      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4">
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
  );
}

