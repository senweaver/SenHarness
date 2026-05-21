"use client";

import { use, useMemo } from "react";
import { Link, useRouter } from "@/lib/navigation";
import { useSearchParams } from "next/navigation";
import {
  IconArrowLeft,
  IconEdit,
  IconMessagePlus,
  IconRobot,
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
import { Separator } from "@/components/ui/separator";
import { Skeleton } from "@/components/ui/skeleton";
import {
  Tabs,
  TabsContent,
  TabsList,
  TabsTrigger,
} from "@/components/ui/tabs";
import { useAgents } from "@/hooks/use-agents";
import { useSquad } from "@/hooks/use-squads";
import { useRecentSessions } from "@/hooks/use-sessions";
import { relativeTime } from "@/lib/utils";
import { SquadBoardBody } from "@/components/squads/SquadBoardBody";

type SquadTab = "detail" | "board";

export default function SquadDetailPage({
  params,
}: {
  params: Promise<{ squadId: string }>;
}) {
  const { squadId } = use(params);
  const locale = useLocale();
  const t = useTranslations("settings.squads.detail");
  const tSquads = useTranslations("settings.squads");
  const tCommon = useTranslations("common");
  const tTabs = useTranslations("squads.tabs");
  const router = useRouter();
  const searchParams = useSearchParams();

  const activeTab: SquadTab =
    searchParams.get("tab") === "board" ? "board" : "detail";

  const onTabChange = (value: string) => {
    const next = new URLSearchParams(searchParams);
    if (value === "detail") {
      next.delete("tab");
    } else {
      next.set("tab", value);
    }
    const qs = next.toString();
    router.replace(`/squads/${squadId}${qs ? `?${qs}` : ""}`);
  };

  const { data: squad, isLoading, error } = useSquad(squadId);
  const { data: agents } = useAgents();
  const { data: sessions } = useRecentSessions(50);

  const agentsById = useMemo(() => {
    type AgentEntry = NonNullable<typeof agents>[number];
    const out: Record<string, AgentEntry | undefined> = {};
    (agents ?? []).forEach((a) => {
      out[a.id] = a;
    });
    return out;
  }, [agents]);

  const squadSessions = useMemo(
    () =>
      (sessions ?? [])
        .filter((s) => s.kind === "squad" && s.subject_id === squadId)
        .slice(0, 8),
    [sessions, squadId],
  );

  if (isLoading) {
    return (
      <div className="p-6">
        <Skeleton className="mb-4 h-8 w-48" />
        <div className="grid gap-4 lg:grid-cols-3">
          <Skeleton className="h-64 lg:col-span-2" />
          <Skeleton className="h-64" />
        </div>
      </div>
    );
  }

  if (error || !squad) {
    return (
      <div className="p-6">
        <PageHeader title={t("notFoundTitle")} description={t("notFoundDesc")} />
        <Button asChild variant="outline">
          <Link href="/squads">
            <IconArrowLeft className="size-4" />
            {t("backToList")}
          </Link>
        </Button>
      </div>
    );
  }

  return (
    <div className="p-6">
      <PageHeader
        title={squad.name}
        description={squad.description ?? t("noDescription")}
        actions={
          <div className="flex flex-wrap items-center gap-2">
            <Button asChild variant="outline" size="sm">
              <Link href="/squads">
                <IconArrowLeft className="size-4" />
                {tCommon("back")}
              </Link>
            </Button>
            <Button asChild variant="outline" size="sm">
              <Link href={`/squads/${squad.id}/edit`}>
                <IconEdit className="size-4" />
                {tSquads("edit")}
              </Link>
            </Button>
            <Button asChild size="sm">
              <Link href={`/chat/new?squad=${squad.id}`}>
                <IconMessagePlus className="size-4" />
                {t("startChat")}
              </Link>
            </Button>
          </div>
        }
      />

      <Tabs value={activeTab} onValueChange={onTabChange}>
        <TabsList>
          <TabsTrigger value="detail">{tTabs("detail")}</TabsTrigger>
          <TabsTrigger value="board">{tTabs("board")}</TabsTrigger>
        </TabsList>
        <TabsContent value="detail">
          <div className="grid gap-4 lg:grid-cols-3">
            <div className="space-y-4 lg:col-span-2">
              <Card>
                <CardHeader>
                  <div className="flex items-center gap-3">
                <div className="flex size-12 items-center justify-center rounded-full bg-black/10 dark:bg-white/10">
                  <IconUsers className="size-6" />
                </div>
                <div className="min-w-0">
                  <CardTitle className="truncate">{squad.name}</CardTitle>
                  {squad.description && (
                    <CardDescription className="truncate">
                      {squad.description}
                    </CardDescription>
                  )}
                </div>
              </div>
            </CardHeader>
            <CardContent>
              <div className="flex flex-wrap gap-1.5">
                <Badge variant="outline">{squad.strategy}</Badge>
                <Badge variant="default">
                  {squad.members.length} {t("membersBadge")}
                </Badge>
              </div>
            </CardContent>
          </Card>

          <Card>
            <CardHeader>
              <CardTitle className="text-base">{t("membersTitle")}</CardTitle>
              <CardDescription>{t("membersDesc")}</CardDescription>
            </CardHeader>
            <CardContent>
              {squad.members.length === 0 ? (
                <p className="text-sm sh-muted">{t("membersEmpty")}</p>
              ) : (
                <ul className="flex flex-col gap-1">
                  {[...squad.members]
                    .sort((a, b) => a.weight - b.weight)
                    .map((m) => {
                      const a = agentsById[m.agent_id];
                      return (
                        <li key={m.id}>
                          <Link
                            href={a ? `/agents/${a.id}` : "#"}
                            className="flex items-center gap-2 rounded-md px-2 py-1.5 hover:bg-black/5 dark:hover:bg-white/10"
                          >
                            {a?.avatar_url ? (
                              <img
                                src={a.avatar_url}
                                alt=""
                                className="size-7 rounded-full object-cover"
                              />
                            ) : (
                              <div className="flex size-7 items-center justify-center rounded-full bg-black/10 dark:bg-white/10">
                                <IconRobot className="size-4" />
                              </div>
                            )}
                            <div className="min-w-0 flex-1">
                              <div className="truncate text-sm font-medium">
                                {a?.name ?? m.agent_id}
                              </div>
                              {a?.description && (
                                <div className="truncate text-[11px] sh-muted">
                                  {a.description}
                                </div>
                              )}
                            </div>
                            <Badge variant="outline">{m.role_in_squad}</Badge>
                          </Link>
                        </li>
                      );
                    })}
                </ul>
              )}
            </CardContent>
          </Card>
        </div>

        <div className="space-y-4">
          <Card>
            <CardHeader>
              <CardTitle className="text-base">{t("infoTitle")}</CardTitle>
            </CardHeader>
            <CardContent className="space-y-2 text-sm">
              <Row label={t("strategyLabel")} value={squad.strategy} />
              <Row
                label={t("memberCountLabel")}
                value={String(squad.members.length)}
              />
              <Separator className="my-2" />
              <Row
                label={t("createdLabel")}
                value={relativeTime(squad.created_at, locale)}
                hint={new Date(squad.created_at).toLocaleString(locale)}
              />
              <Row
                label={t("updatedLabel")}
                value={relativeTime(squad.updated_at, locale)}
                hint={new Date(squad.updated_at).toLocaleString(locale)}
              />
              <Row label={t("idLabel")} value={squad.id} mono />
            </CardContent>
          </Card>

          <Card>
            <CardHeader>
              <CardTitle className="text-base">
                {t("sessionsTitle")}
              </CardTitle>
              <CardDescription>{t("sessionsDesc")}</CardDescription>
            </CardHeader>
            <CardContent>
              {squadSessions.length === 0 ? (
                <p className="text-sm sh-muted">{t("sessionsEmpty")}</p>
              ) : (
                <ul className="-mx-2 flex flex-col gap-0.5">
                  {squadSessions.map((s) => (
                    <li key={s.id}>
                      <Link
                        href={`/chat/${s.id}`}
                        className="flex items-center gap-2 rounded-md px-2 py-1.5 text-sm hover:bg-black/5 dark:hover:bg-white/10"
                      >
                        <span className="flex-1 truncate">
                          {s.title ?? t("untitledSession")}
                        </span>
                        {s.last_message_at && (
                          <span className="text-[11px] sh-muted tabular-nums">
                            {relativeTime(s.last_message_at, locale)}
                          </span>
                        )}
                      </Link>
                    </li>
                  ))}
                </ul>
              )}
            </CardContent>
          </Card>
        </div>
      </div>
        </TabsContent>
        <TabsContent value="board">
          <SquadBoardBody squadId={squadId} />
        </TabsContent>
      </Tabs>
    </div>
  );
}

function Row({
  label,
  value,
  hint,
  mono,
}: {
  label: string;
  value: string;
  hint?: string;
  mono?: boolean;
}) {
  return (
    <div className="flex items-start justify-between gap-3">
      <span className="shrink-0 text-[12px] sh-muted">{label}</span>
      <span
        className={mono ? "truncate font-mono text-[11px]" : "truncate text-[13px]"}
        title={hint ?? value}
      >
        {value}
      </span>
    </div>
  );
}
