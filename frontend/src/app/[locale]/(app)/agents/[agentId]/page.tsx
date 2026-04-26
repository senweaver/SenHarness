"use client";

import { use, useMemo } from "react";
import { Link } from "@/lib/navigation";
import {
  IconArrowLeft,
  IconEdit,
  IconMessagePlus,
  IconRobot,
  IconStar,
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
import { PageHeader } from "@/components/ui/page-header";
import { Separator } from "@/components/ui/separator";
import { Skeleton } from "@/components/ui/skeleton";
import {
  useAgent,
  useIsAgentStarred,
  useToggleStar,
} from "@/hooks/use-agent-mutations";
import { useRecentSessions } from "@/hooks/use-sessions";
import { relativeTime } from "@/lib/utils";

export default function AgentDetailPage({
  params,
}: {
  params: Promise<{ agentId: string }>;
}) {
  const { agentId } = use(params);
  const locale = useLocale();
  const t = useTranslations("settings.agents.detail");
  const tAgents = useTranslations("settings.agents");
  const tCommon = useTranslations("common");

  const { data: agent, isLoading, error } = useAgent(agentId);
  const { data: starredList } = useIsAgentStarred(agentId);
  const toggleStar = useToggleStar(agentId);
  const { data: sessions } = useRecentSessions(50);

  const starred = useMemo(
    () => Boolean((starredList ?? []).some((a) => a.id === agentId)),
    [starredList, agentId],
  );

  const agentSessions = useMemo(
    () =>
      (sessions ?? [])
        .filter((s) => s.kind === "p2p" && s.subject_id === agentId)
        .slice(0, 8),
    [sessions, agentId],
  );

  const meta = (agent?.metadata_json ?? {}) as Record<string, unknown>;
  const codeMode = Boolean(meta.code_mode);
  const requireApproval = Boolean(meta.approvals);
  const sandboxVal = meta.sandbox;
  const sandboxLabel =
    sandboxVal === false || sandboxVal === null || sandboxVal === undefined
      ? t("sandboxOff")
      : sandboxVal === true || sandboxVal === "local"
        ? t("sandboxLocal")
        : sandboxVal === "docker" ||
            (typeof sandboxVal === "object" &&
              sandboxVal !== null &&
              (sandboxVal as Record<string, unknown>).kind === "docker")
          ? t("sandboxDocker")
          : t("sandboxLocal");

  const onToggleStar = async () => {
    try {
      await toggleStar.mutateAsync({ starred: !starred });
      toast.success(starred ? t("unstarred") : t("starred"));
    } catch {
      toast.error(t("starFailed"));
    }
  };

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

  if (error || !agent) {
    return (
      <div className="p-6">
        <PageHeader title={t("notFoundTitle")} description={t("notFoundDesc")} />
        <Button asChild variant="outline">
          <Link href="/agents">
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
        title={agent.name}
        description={agent.description ?? t("noDescription")}
        actions={
          <div className="flex flex-wrap items-center gap-2">
            <Button asChild variant="outline" size="sm">
              <Link href="/agents">
                <IconArrowLeft className="size-4" />
                {tCommon("back")}
              </Link>
            </Button>
            <Button
              variant="outline"
              size="sm"
              onClick={onToggleStar}
              disabled={toggleStar.isPending}
              aria-pressed={starred}
            >
              {starred ? (
                <IconStarFilled className="size-4 text-yellow-500" />
              ) : (
                <IconStar className="size-4" />
              )}
              {starred ? t("unstar") : t("star")}
            </Button>
            <Button asChild variant="outline" size="sm">
              <Link href={`/agents/${agent.id}/edit`}>
                <IconEdit className="size-4" />
                {tAgents("edit")}
              </Link>
            </Button>
            <Button asChild size="sm">
              <Link href={`/chat/new?agent=${agent.id}`}>
                <IconMessagePlus className="size-4" />
                {t("startChat")}
              </Link>
            </Button>
          </div>
        }
      />

      <div className="grid gap-4 lg:grid-cols-3">
        <div className="space-y-4 lg:col-span-2">
          <Card>
            <CardHeader>
              <div className="flex items-center gap-3">
                {agent.avatar_url ? (
                  <img
                    src={agent.avatar_url}
                    alt=""
                    className="size-12 rounded-full object-cover"
                  />
                ) : (
                  <div className="flex size-12 items-center justify-center rounded-full bg-black/10 dark:bg-white/10">
                    <IconRobot className="size-6" />
                  </div>
                )}
                <div className="min-w-0">
                  <CardTitle className="truncate">{agent.name}</CardTitle>
                  {agent.description && (
                    <CardDescription className="truncate">
                      {agent.description}
                    </CardDescription>
                  )}
                </div>
              </div>
            </CardHeader>
            <CardContent>
              <div className="flex flex-wrap gap-1.5">
                <Badge variant="outline">{agent.backend_kind}</Badge>
                <Badge variant="default">
                  {agent.autonomy_level.toUpperCase()}
                </Badge>
                <Badge variant="default">{agent.visibility}</Badge>
                {codeMode && <Badge variant="primary">CodeMode</Badge>}
                {requireApproval && (
                  <Badge variant="primary">{t("hitl")}</Badge>
                )}
              </div>
            </CardContent>
          </Card>

          <Card>
            <CardHeader>
              <CardTitle className="text-base">{t("personaTitle")}</CardTitle>
              <CardDescription>{t("personaDesc")}</CardDescription>
            </CardHeader>
            <CardContent>
              {agent.persona_md ? (
                <pre className="whitespace-pre-wrap break-words rounded-md bg-black/5 p-4 font-mono text-[13px] dark:bg-white/5">
                  {agent.persona_md}
                </pre>
              ) : (
                <p className="text-sm sh-muted">{t("personaEmpty")}</p>
              )}
            </CardContent>
          </Card>
        </div>

        <div className="space-y-4">
          <Card>
            <CardHeader>
              <CardTitle className="text-base">{t("runtimeTitle")}</CardTitle>
            </CardHeader>
            <CardContent className="space-y-2 text-sm">
              <Row label={t("backendLabel")} value={agent.backend_kind} />
              <Row
                label={t("autonomyLabel")}
                value={agent.autonomy_level.toUpperCase()}
              />
              <Row label={t("visibilityLabel")} value={agent.visibility} />
              <Row label={t("sandboxLabel")} value={sandboxLabel} />
              <Row
                label={t("codeModeLabel")}
                value={codeMode ? t("enabled") : t("disabled")}
              />
              <Row
                label={t("approvalsLabel")}
                value={requireApproval ? t("enabled") : t("disabled")}
              />
              <Separator className="my-2" />
              <Row
                label={t("createdLabel")}
                value={relativeTime(agent.created_at, locale)}
                hint={new Date(agent.created_at).toLocaleString(locale)}
              />
              <Row
                label={t("updatedLabel")}
                value={relativeTime(agent.updated_at, locale)}
                hint={new Date(agent.updated_at).toLocaleString(locale)}
              />
              <Row label={t("idLabel")} value={agent.id} mono />
            </CardContent>
          </Card>

          <Card>
            <CardHeader>
              <CardTitle className="text-base">{t("sessionsTitle")}</CardTitle>
              <CardDescription>{t("sessionsDesc")}</CardDescription>
            </CardHeader>
            <CardContent>
              {agentSessions.length === 0 ? (
                <p className="text-sm sh-muted">{t("sessionsEmpty")}</p>
              ) : (
                <ul className="-mx-2 flex flex-col gap-0.5">
                  {agentSessions.map((s) => (
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
        className={
          mono
            ? "truncate font-mono text-[11px]"
            : "truncate text-[13px]"
        }
        title={hint ?? value}
      >
        {value}
      </span>
    </div>
  );
}
