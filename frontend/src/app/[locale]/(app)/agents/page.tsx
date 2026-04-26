"use client";

import { Link } from "@/lib/navigation";
import {
  IconEdit,
  IconInfoCircle,
  IconPlus,
  IconRobot,
  IconSparkles,
} from "@tabler/icons-react";
import { useTranslations } from "next-intl";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { PageHeader } from "@/components/ui/page-header";
import { Skeleton } from "@/components/ui/skeleton";
import { useAgents } from "@/hooks/use-agents";
import { useAgentTerm } from "@/components/nav/AgentTermLabel";

export default function AgentsPage() {
  const t = useTranslations();
  const tAgents = useTranslations("settings.agents");
  const term = useAgentTerm();
  const { data, isLoading } = useAgents();

  return (
    <div className="p-6">
      <PageHeader
        title={term}
        description={tAgents("description")}
        actions={
          <Button asChild size="sm">
            <Link href="/agents/new">
              <IconPlus className="size-4" />
              {tAgents("new")}
            </Link>
          </Button>
        }
      />

      {isLoading && (
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3">
          {[0, 1, 2].map((i) => (
            <Skeleton key={i} className="h-28" />
          ))}
        </div>
      )}

      {!isLoading && (data ?? []).length === 0 && (
        <Card>
          <CardContent className="flex flex-col items-center gap-2 py-10 text-center">
            <IconSparkles className="size-8 sh-muted" />
            <p className="text-sm sh-muted">{tAgents("empty")}</p>
            <Button asChild size="sm" variant="outline">
              <Link href="/agents/new">
                <IconPlus className="size-4" />
                {tAgents("new")}
              </Link>
            </Button>
          </CardContent>
        </Card>
      )}

      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3">
        {(data ?? []).map((a) => {
          const codeMode = Boolean((a.metadata_json as { code_mode?: unknown })?.code_mode);
          return (
            <Card key={a.id} className="relative">
              <Link
                href={`/chat/new?agent=${a.id}`}
                className="absolute inset-0"
                aria-label={`chat with ${a.name}`}
              />
              <CardHeader>
                <div className="flex items-center gap-2">
                  {a.avatar_url ? (
                    <img src={a.avatar_url} alt="" className="size-8 rounded-full" />
                  ) : (
                    <div className="flex size-8 items-center justify-center rounded-full bg-black/10 dark:bg-white/10">
                      <IconRobot className="size-4" />
                    </div>
                  )}
                  <CardTitle className="flex-1 truncate">{a.name}</CardTitle>
                </div>
                {a.description && <CardDescription>{a.description}</CardDescription>}
              </CardHeader>
              <CardContent className="flex flex-wrap items-center gap-1.5 pt-0">
                <Badge variant="outline">{a.backend_kind}</Badge>
                <Badge variant="default">{a.autonomy_level.toUpperCase()}</Badge>
                <Badge variant="default">{a.visibility}</Badge>
                {codeMode && <Badge variant="primary">CodeMode</Badge>}
                <div className="ml-auto flex items-center gap-1">
                  <Button
                    asChild
                    variant="ghost"
                    size="icon"
                    className="relative z-10 size-7"
                    onClick={(e) => e.stopPropagation()}
                  >
                    <Link
                      href={`/agents/${a.id}`}
                      aria-label={tAgents("details")}
                      onClick={(e) => e.stopPropagation()}
                    >
                      <IconInfoCircle className="size-3.5" />
                    </Link>
                  </Button>
                  <Button
                    asChild
                    variant="ghost"
                    size="icon"
                    className="relative z-10 size-7"
                    onClick={(e) => e.stopPropagation()}
                  >
                    <Link
                      href={`/agents/${a.id}/edit`}
                      aria-label={tAgents("edit")}
                      onClick={(e) => e.stopPropagation()}
                    >
                      <IconEdit className="size-3.5" />
                    </Link>
                  </Button>
                </div>
              </CardContent>
            </Card>
          );
        })}
      </div>

      <span className="sr-only">{t("common.loading")}</span>
    </div>
  );
}
