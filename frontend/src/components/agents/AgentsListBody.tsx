"use client";

import { Link } from "@/lib/navigation";
import { IconPlus, IconSparkles } from "@tabler/icons-react";
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
import { Skeleton } from "@/components/ui/skeleton";
import { useAgents } from "@/hooks/use-agents";

interface AgentsListBodyProps {
  onNew: () => void;
}

export function AgentsListBody({ onNew }: AgentsListBodyProps) {
  const tAgents = useTranslations("settings.agents");
  const { data, isLoading } = useAgents();

  if (isLoading) {
    return (
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3">
        {[0, 1, 2].map((i) => (
          <Skeleton key={i} className="h-28" />
        ))}
      </div>
    );
  }

  if (!isLoading && (data ?? []).length === 0) {
    return (
      <Card>
        <CardContent className="flex flex-col items-center gap-2 py-10 text-center">
          <IconSparkles className="size-8 sh-muted" />
          <p className="text-sm sh-muted">{tAgents("empty")}</p>
          <Button size="sm" variant="outline" onClick={onNew}>
            <IconPlus className="size-4" />
            {tAgents("new")}
          </Button>
        </CardContent>
      </Card>
    );
  }

  return (
    <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3">
      {(data ?? []).map((a) => {
        const codeMode = Boolean(
          (a.metadata_json as { code_mode?: unknown })?.code_mode,
        );
        return (
          <Card key={a.id} className="relative">
            <Link
              href={`/agents/${a.id}`}
              className="absolute inset-0 z-[1]"
              aria-label={`open ${a.name}`}
            />
            <CardHeader>
              <div className="flex items-center gap-2">
                {a.avatar_url ? (
                  /* eslint-disable-next-line @next/next/no-img-element */
                  <img
                    src={a.avatar_url}
                    alt=""
                    className="size-8 rounded-full object-cover"
                  />
                ) : (
                  <div className="flex size-8 items-center justify-center rounded-full bg-[rgb(var(--color-primary)/0.12)] text-sm font-semibold text-[rgb(var(--color-primary))]">
                    {a.name.slice(0, 1).toUpperCase() || "?"}
                  </div>
                )}
                <CardTitle className="flex-1 truncate">{a.name}</CardTitle>
              </div>
              {a.description && (
                <CardDescription>{a.description}</CardDescription>
              )}
            </CardHeader>
            <CardContent className="flex flex-wrap items-center gap-1.5 pt-0">
              <Badge variant="outline">{a.backend_kind}</Badge>
              <Badge variant="default">{a.autonomy_level.toUpperCase()}</Badge>
              <Badge variant="default">{a.visibility}</Badge>
              {codeMode && <Badge variant="primary">CodeMode</Badge>}
            </CardContent>
          </Card>
        );
      })}
    </div>
  );
}
