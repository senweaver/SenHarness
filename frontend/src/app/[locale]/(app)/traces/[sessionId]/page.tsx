"use client";

import { use, useMemo, useState } from "react";
import { Link } from "@/lib/navigation";
import { useTranslations } from "next-intl";
import {
  IconArrowBack,
  IconChecklist,
  IconCircleDashed,
  IconCoin,
  IconGauge,
  IconMessage2,
  IconRobot,
  IconSparkles,
  IconTerminal2,
  IconTool,
  IconUser,
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
import { PageHeader } from "@/components/ui/page-header";
import { Button } from "@/components/ui/button";
import { LineageDrawer } from "@/components/chat/LineageDrawer";
import { useSessionTrace, type TraceEvent, type TraceRole } from "@/hooks/use-traces";

type Params = Promise<{ sessionId: string }>;

export default function TraceReplayPage({ params }: { params: Params }) {
  const { sessionId } = use(params);
  const t = useTranslations("trace");
  const tLineage = useTranslations("lineage");
  const { data, isLoading, isError } = useSessionTrace(sessionId);
  const [filter, setFilter] = useState<"all" | "messages" | "tools" | "thinking">(
    "all",
  );
  const [lineageMessageId, setLineageMessageId] = useState<string | null>(null);

  const events = data?.events ?? [];
  const visible = useMemo(() => {
    if (filter === "all") return events;
    return events.filter((e) => {
      if (filter === "messages")
        return e.role === "user" || e.role === "assistant";
      if (filter === "tools")
        return e.role === "tool_call" || e.role === "tool_result";
      if (filter === "thinking") return e.role === "thinking";
      return true;
    });
  }, [events, filter]);

  const verdicts = useMemo(() => {
    const counts = { pass: 0, warn: 0, fail: 0 };
    for (const v of data?.summary.eval_verdicts ?? []) {
      if (!v) continue;
      const key = String((v as Record<string, unknown>).verdict ?? "") as
        | "pass"
        | "warn"
        | "fail";
      if (key in counts) counts[key] += 1;
    }
    return counts;
  }, [data?.summary.eval_verdicts]);

  return (
    <div className="space-y-4 p-6">
      <PageHeader
        title={data?.title ? `${t("title")} · ${data.title}` : t("title")}
        description={t("description")}
        actions={
          <div className="flex items-center gap-2">
            <Link href={`/chat/${sessionId}`}>
              <Button variant="outline" size="sm">
                <IconArrowBack className="size-4" />
                {t("backToChat")}
              </Button>
            </Link>
          </div>
        }
      />

      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <IconGauge className="size-4" />
            {t("summary")}
          </CardTitle>
          <CardDescription>
            {t("eventsCount", { count: data?.event_count ?? 0 })}
          </CardDescription>
        </CardHeader>
        <CardContent>
          <div className="flex flex-wrap gap-4 text-xs">
            <SummaryStat
              icon={<IconSparkles className="size-3.5" />}
              label={t("tokensIn")}
              value={(data?.summary.tokens.input ?? 0).toLocaleString()}
            />
            <SummaryStat
              icon={<IconSparkles className="size-3.5" />}
              label={t("tokensOut")}
              value={(data?.summary.tokens.output ?? 0).toLocaleString()}
            />
            <SummaryStat
              icon={<IconCoin className="size-3.5" />}
              label={t("cost")}
              value={`$${(data?.summary.cost_usd ?? 0).toFixed(4)}`}
            />
            <SummaryStat
              icon={<IconChecklist className="size-3.5" />}
              label={t("verdicts")}
              value={`${verdicts.pass}✓ · ${verdicts.warn}⚠ · ${verdicts.fail}✗`}
            />
          </div>
        </CardContent>
      </Card>

      <div className="flex gap-1.5">
        {(["all", "messages", "tools", "thinking"] as const).map((k) => (
          <button
            key={k}
            onClick={() => setFilter(k)}
            className={
              "rounded-full border px-3 py-1 text-[11px] transition " +
              (filter === k
                ? "border-[rgb(var(--color-primary))] text-[rgb(var(--color-primary))] bg-black/5 dark:bg-white/5"
                : "sh-muted hover:bg-black/5 dark:hover:bg-white/5")
            }
          >
            {t(`filter.${k}`)}
          </button>
        ))}
      </div>

      {isLoading && <Skeleton className="h-64" />}
      {isError && (
        <Card>
          <CardContent className="py-10 text-center text-sm sh-muted">
            {t("loadFailed")}
          </CardContent>
        </Card>
      )}
      {!isLoading && !isError && visible.length === 0 && (
        <Card>
          <CardContent className="py-10 text-center text-sm sh-muted">
            {t("empty")}
          </CardContent>
        </Card>
      )}

      <ol className="relative space-y-3 border-l border-black/10 pl-5 dark:border-white/15">
        {visible.map((ev) => (
          <TraceRow
            key={ev.message_id}
            event={ev}
            onExpandLineage={setLineageMessageId}
            expandLabel={tLineage("expandFromSummary")}
            compressedBadgeLabel={(count) =>
              tLineage("compressedBadge", { count })
            }
          />
        ))}
      </ol>

      <LineageDrawer
        sessionId={sessionId}
        messageId={lineageMessageId}
        open={lineageMessageId !== null}
        onOpenChange={(next) => {
          if (!next) setLineageMessageId(null);
        }}
      />
    </div>
  );
}

function SummaryStat({
  icon,
  label,
  value,
}: {
  icon: React.ReactNode;
  label: string;
  value: string;
}) {
  return (
    <div className="flex items-center gap-2 rounded-md border px-3 py-2">
      {icon}
      <div className="flex flex-col">
        <span className="text-[10px] uppercase sh-muted">{label}</span>
        <span className="font-mono text-[13px]">{value}</span>
      </div>
    </div>
  );
}

const ROLE_META: Record<
  TraceRole,
  { label: string; tone: "default" | "primary" | "success" | "warning" | "danger" | "outline"; icon: React.ReactNode }
> = {
  user: { label: "user", tone: "primary", icon: <IconUser className="size-3.5" /> },
  assistant: {
    label: "assistant",
    tone: "success",
    icon: <IconRobot className="size-3.5" />,
  },
  system: {
    label: "system",
    tone: "outline",
    icon: <IconCircleDashed className="size-3.5" />,
  },
  tool_call: {
    label: "tool_call",
    tone: "default",
    icon: <IconTool className="size-3.5" />,
  },
  tool_result: {
    label: "tool_result",
    tone: "default",
    icon: <IconTerminal2 className="size-3.5" />,
  },
  thinking: {
    label: "thinking",
    tone: "outline",
    icon: <IconSparkles className="size-3.5" />,
  },
  approval: {
    label: "approval",
    tone: "warning",
    icon: <IconChecklist className="size-3.5" />,
  },
  handoff: {
    label: "handoff",
    tone: "default",
    icon: <IconMessage2 className="size-3.5" />,
  },
};

interface TraceRowProps {
  event: TraceEvent;
  onExpandLineage: (messageId: string) => void;
  expandLabel: string;
  compressedBadgeLabel: (count: number) => string;
}

function TraceRow({
  event,
  onExpandLineage,
  expandLabel,
  compressedBadgeLabel,
}: TraceRowProps) {
  const meta = ROLE_META[event.role] ?? ROLE_META.system;
  const text = extractText(event);
  const ts = event.created_at ? new Date(event.created_at).toLocaleTimeString() : "—";
  const verdict = event.metadata?.eval;
  const tokenUsage = event.token_usage as {
    tokens?: { input?: number; output?: number };
    latency_ms?: number;
  };
  const compressedTurns = event.original_turns_ref?.turn_count ?? 0;
  const isCompressedSummary = compressedTurns > 0;

  return (
    <li className="relative">
      <span className="absolute -left-[11px] top-1.5 flex size-5 items-center justify-center rounded-full border bg-[rgb(var(--color-bg))]">
        {meta.icon}
      </span>
      <div className="rounded-md border p-3">
        <div className="mb-1 flex flex-wrap items-center gap-2 text-[11px]">
          <Badge variant={meta.tone}>{meta.label}</Badge>
          <span className="sh-muted font-mono">{ts}</span>
          {event.metadata?.run_id && (
            <span className="sh-muted font-mono">
              run {String(event.metadata.run_id).slice(0, 8)}
            </span>
          )}
          {tokenUsage?.tokens && (
            <span className="sh-muted font-mono">
              {(tokenUsage.tokens.input ?? 0) + (tokenUsage.tokens.output ?? 0)} tok
            </span>
          )}
          {typeof tokenUsage?.latency_ms === "number" && (
            <span className="sh-muted font-mono">{tokenUsage.latency_ms}ms</span>
          )}
          {isCompressedSummary && (
            <>
              <Badge variant="warning">
                {compressedBadgeLabel(compressedTurns)}
              </Badge>
              <button
                type="button"
                onClick={() => onExpandLineage(event.message_id)}
                className="rounded-full border px-2 py-0.5 text-[10px] transition hover:bg-black/5 dark:hover:bg-white/5"
              >
                {expandLabel}
              </button>
            </>
          )}
          {verdict && (
            <Badge
              variant={
                verdict.verdict === "pass"
                  ? "success"
                  : verdict.verdict === "warn"
                    ? "warning"
                    : "danger"
              }
            >
              eval {verdict.verdict} · {verdict.score?.toFixed(2) ?? "—"}
            </Badge>
          )}
        </div>
        {event.role === "tool_call" && event.tool_call ? (
          <pre className="whitespace-pre-wrap break-all rounded bg-black/5 p-2 text-[11px] dark:bg-white/5">
            {safeJson(event.tool_call)}
          </pre>
        ) : null}
        {event.role === "tool_result" && event.tool_result ? (
          <pre className="whitespace-pre-wrap break-all rounded bg-black/5 p-2 text-[11px] dark:bg-white/5">
            {safeJson(event.tool_result)}
          </pre>
        ) : null}
        {event.role === "thinking" && event.thinking ? (
          <p className="whitespace-pre-wrap text-[12px] italic sh-muted">
            {String((event.thinking as Record<string, unknown>).text ?? "")}
          </p>
        ) : null}
        {(event.role === "user" || event.role === "assistant") && text && (
          <p className="whitespace-pre-wrap text-sm leading-relaxed">{text}</p>
        )}
      </div>
    </li>
  );
}

function extractText(event: TraceEvent): string {
  const c = event.content as Record<string, unknown>;
  if (typeof c.text === "string") return c.text;
  if (Array.isArray(c.parts)) {
    return c.parts
      .map((p) => (typeof p === "string" ? p : (p as Record<string, unknown>).text ?? ""))
      .filter(Boolean)
      .join("\n");
  }
  return "";
}

function safeJson(v: unknown): string {
  try {
    const s = JSON.stringify(v, null, 2);
    return s.length > 2000 ? s.slice(0, 2000) + "\n… (truncated)" : s;
  } catch {
    return String(v);
  }
}
