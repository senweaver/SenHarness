"use client";

import { useTranslations } from "next-intl";
import {
  IconBolt,
  IconCheck,
  IconCircleDot,
  IconLink,
  IconX,
} from "@tabler/icons-react";

import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import { useRegisteredRuntimes, type RegisteredRuntime } from "@/hooks/use-runtimes";

export function RegisteredRuntimesCard() {
  const t = useTranslations("settings.runtimes.registry");
  const { data, isLoading } = useRegisteredRuntimes();

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <IconBolt className="size-4 text-[rgb(var(--color-primary))]" />
          {t("title")}
        </CardTitle>
        <CardDescription>{t("description")}</CardDescription>
      </CardHeader>
      <CardContent>
        {isLoading && <Skeleton className="h-24" />}
        {!isLoading && (data ?? []).length === 0 && (
          <p className="text-sm sh-muted">{t("empty")}</p>
        )}
        <div className="grid gap-2 sm:grid-cols-2">
          {(data ?? []).map((r) => (
            <RuntimeRow key={r.kind} runtime={r} />
          ))}
        </div>
      </CardContent>
    </Card>
  );
}

function RuntimeRow({ runtime }: { runtime: RegisteredRuntime }) {
  const t = useTranslations("settings.runtimes.registry");
  const caps = runtime.capabilities;
  const chips: Array<[boolean, string]> = [
    [caps.supports_streaming, "streaming"],
    [caps.supports_parallel_tools, "parallel tools"],
    [caps.supports_thinking, "thinking"],
    [caps.supports_native_mcp, "native MCP"],
    [caps.supports_vision, "vision"],
  ];

  return (
    <div className="rounded-md border p-3">
      <div className="mb-1 flex items-center gap-2">
        <IconCircleDot className="size-3.5 text-emerald-500" />
        <span className="text-sm font-semibold">{runtime.display_name}</span>
        <Badge variant="outline">{runtime.kind}</Badge>
        {runtime.requires_adapter && (
          <Badge variant="warning">{t("requiresAdapter")}</Badge>
        )}
      </div>
      {runtime.description && (
        <p className="mb-2 text-xs sh-muted">{runtime.description}</p>
      )}
      <div className="mb-1 flex flex-wrap gap-1.5">
        {chips.map(([ok, label]) => (
          <span
            key={label}
            className={
              "inline-flex items-center gap-0.5 rounded-full border px-2 py-0.5 text-[10px] " +
              (ok
                ? "border-emerald-500/40 text-emerald-600"
                : "border-black/10 text-[rgb(var(--color-muted))] dark:border-white/10")
            }
          >
            {ok ? <IconCheck className="size-3" /> : <IconX className="size-3" />}
            {label}
          </span>
        ))}
        {caps.max_context_tokens ? (
          <span className="inline-flex items-center gap-0.5 rounded-full border px-2 py-0.5 text-[10px] sh-muted">
            ctx ≤ {caps.max_context_tokens.toLocaleString()}
          </span>
        ) : null}
      </div>
      {runtime.docs_url && (
        <a
          href={runtime.docs_url}
          target="_blank"
          rel="noreferrer"
          className="inline-flex items-center gap-1 text-[11px] text-[rgb(var(--color-primary))] hover:underline"
        >
          <IconLink className="size-3" />
          {t("docs")}
        </a>
      )}
    </div>
  );
}
