"use client";

import { useState } from "react";
import { useTranslations } from "next-intl";
import { toast } from "sonner";
import {
  IconHeartbeat,
  IconLoader2,
  IconRefresh,
  IconTrash,
} from "@tabler/icons-react";

import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { PageHeader } from "@/components/ui/page-header";
import { McpServerForm } from "@/components/mcp/McpServerForm";
import {
  useDeleteMcpServer,
  useListMcpServerTools,
  useMcpServers,
  usePingMcpServer,
  type McpServerRead,
} from "@/hooks/use-mcp-servers";

const HEALTH_STYLES: Record<
  McpServerRead["health_status"],
  { badge: "success" | "warning" | "default" | "danger"; label: string }
> = {
  healthy: { badge: "success", label: "healthy" },
  degraded: { badge: "warning", label: "degraded" },
  down: { badge: "danger", label: "down" },
  unknown: { badge: "default", label: "—" },
};

export default function McpSettingsPage() {
  const t = useTranslations("mcp");
  const { data, isLoading } = useMcpServers();
  const remove = useDeleteMcpServer();

  return (
    <div className="space-y-6">
      <PageHeader title={t("pageTitle")} description={t("pageDescription")} />

      <McpServerForm />

      <section className="space-y-3">
        <h2 className="text-sm font-semibold">{t("registeredHeading")}</h2>
        {isLoading && <Skeleton className="h-24" />}

        {!isLoading && (data ?? []).length === 0 && (
          <Card>
            <CardContent className="py-10 text-center text-sm sh-muted">
              {t("empty")}
            </CardContent>
          </Card>
        )}

        <div className="grid gap-3 md:grid-cols-2">
          {(data ?? []).map((server) => (
            <ServerCard
              key={server.id}
              server={server}
              onDelete={async () => {
                if (!confirm(t("confirmDelete"))) return;
                try {
                  await remove.mutateAsync(server.id);
                  toast.success(t("deleted"));
                } catch {
                  toast.error(t("deleteFailed"));
                }
              }}
            />
          ))}
        </div>
      </section>
    </div>
  );
}

function ServerCard({
  server,
  onDelete,
}: {
  server: McpServerRead;
  onDelete: () => Promise<void> | void;
}) {
  const t = useTranslations("mcp");
  const ping = usePingMcpServer(server.id);
  const listTools = useListMcpServerTools(server.id);
  const [tools, setTools] = useState<string[] | null>(null);

  const health = HEALTH_STYLES[server.health_status];
  const lastChecked = server.last_checked_at
    ? new Date(server.last_checked_at).toLocaleString()
    : t("neverChecked");

  return (
    <Card>
      <CardHeader>
        <div className="flex items-center gap-2">
          <CardTitle className="flex-1 truncate">{server.name}</CardTitle>
          <Badge variant="outline">{server.transport}</Badge>
          <Badge variant={health.badge}>{health.label}</Badge>
        </div>
        <CardDescription className="font-mono text-[11px]">
          {server.transport === "stdio"
            ? server.command || t("noCommand")
            : server.url || server.endpoint || t("noUrl")}
          {" · "}
          {t("lastChecked")}: {lastChecked}
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-3 pt-0">
        {server.auth_json?.type === "oauth" && (
          <Badge variant="outline">{t("oauthBadge")}</Badge>
        )}
        {tools && tools.length > 0 && (
          <div className="flex flex-wrap gap-1">
            {tools.map((name) => (
              <Badge key={name} variant="default">
                {name}
              </Badge>
            ))}
          </div>
        )}
        <div className="flex flex-wrap gap-1">
          <Button
            variant="outline"
            size="sm"
            onClick={async () => {
              try {
                const r = await ping.mutateAsync();
                toast.success(`${r.status} — ${r.detail ?? ""}`);
              } catch {
                toast.error(t("pingFailed"));
              }
            }}
            disabled={ping.isPending}
          >
            {ping.isPending ? (
              <IconLoader2 className="size-4 animate-spin" />
            ) : (
              <IconHeartbeat className="size-4" />
            )}
            {t("ping")}
          </Button>
          <Button
            variant="outline"
            size="sm"
            onClick={async () => {
              try {
                const result = await listTools.mutateAsync();
                setTools(result.map((tool) => tool.name));
                toast.success(t("toolsRefreshed", { n: result.length }));
              } catch (e) {
                toast.error(
                  e instanceof Error ? e.message : t("toolsRefreshFailed"),
                );
              }
            }}
            disabled={listTools.isPending}
          >
            {listTools.isPending ? (
              <IconLoader2 className="size-4 animate-spin" />
            ) : (
              <IconRefresh className="size-4" />
            )}
            {t("listTools")}
          </Button>
          <Button variant="ghost" size="sm" onClick={onDelete}>
            <IconTrash className="size-3.5" />
          </Button>
        </div>
      </CardContent>
    </Card>
  );
}
