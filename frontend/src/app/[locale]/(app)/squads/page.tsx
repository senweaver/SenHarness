"use client";

import { Link } from "@/lib/navigation";
import {
  IconEdit,
  IconInfoCircle,
  IconPlus,
  IconSparkles,
  IconUsers,
} from "@tabler/icons-react";
import { useLocale, useTranslations } from "next-intl";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { PageHeader } from "@/components/ui/page-header";
import { Skeleton } from "@/components/ui/skeleton";
import { useSquads } from "@/hooks/use-squads";
import { relativeTime } from "@/lib/utils";

export default function SquadsPage() {
  const t = useTranslations("settings.squads");
  const tCommon = useTranslations("common");
  const locale = useLocale();
  const { data, isLoading } = useSquads();

  return (
    <div className="p-6">
      <PageHeader
        title={t("title")}
        description={t("description")}
        actions={
          <Button asChild size="sm">
            <Link href="/squads/new">
              <IconPlus className="size-4" />
              {t("new")}
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
            <p className="text-sm sh-muted">{t("empty")}</p>
            <Button asChild size="sm" variant="outline">
              <Link href="/squads/new">
                <IconPlus className="size-4" />
                {t("new")}
              </Link>
            </Button>
          </CardContent>
        </Card>
      )}

      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3">
        {(data ?? []).map((s) => (
          <Card key={s.id} className="relative">
            <Link
              href={`/chat/new?squad=${s.id}`}
              className="absolute inset-0"
              aria-label={`chat with ${s.name}`}
            />
            <CardHeader>
              <div className="flex items-center gap-2">
                <div className="flex size-8 items-center justify-center rounded-full bg-black/10 dark:bg-white/10">
                  <IconUsers className="size-4" />
                </div>
                <CardTitle className="flex-1 truncate">{s.name}</CardTitle>
              </div>
              {s.description && (
                <CardDescription>{s.description}</CardDescription>
              )}
            </CardHeader>
            <CardContent className="flex flex-wrap items-center gap-1.5 pt-0">
              <Badge variant="outline">{s.strategy}</Badge>
              <span className="text-[10px] sh-muted">
                {relativeTime(s.updated_at, locale)}
              </span>
              <div className="ml-auto flex items-center gap-1">
                <Button
                  asChild
                  variant="ghost"
                  size="icon"
                  className="relative z-10 size-7"
                  onClick={(e) => e.stopPropagation()}
                >
                  <Link
                    href={`/squads/${s.id}`}
                    aria-label={tCommon("more")}
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
                    href={`/squads/${s.id}/edit`}
                    aria-label={t("edit")}
                    onClick={(e) => e.stopPropagation()}
                  >
                    <IconEdit className="size-3.5" />
                  </Link>
                </Button>
              </div>
            </CardContent>
          </Card>
        ))}
      </div>
    </div>
  );
}
