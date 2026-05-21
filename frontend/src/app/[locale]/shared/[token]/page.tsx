"use client";

import { use, useEffect, useState } from "react";
import { Link } from "@/lib/navigation";
import { useTranslations } from "next-intl";
import {
  IconArrowLeft,
  IconLoader2,
  IconLockOpen,
  IconRobot,
  IconUser,
} from "@tabler/icons-react";
import { Response } from "@/components/ai-elements/response";
import { Button } from "@/components/ui/button";
import { ToolCallCard } from "@/components/chat/ToolCallCard";
import { fetchPublicShare } from "@/hooks/use-session-shares";
import { cn } from "@/lib/utils";
import type { PublicSharedSession } from "@/types/api";

/**
 * `/shared/[token]` — public read-only render of a shared conversation.
 *
 * Hits the unauthenticated ``GET /api/v1/sessions/shared/{token}`` endpoint
 * so anyone with the link can read the transcript. We deliberately keep the
 * UI minimal: assistant bubbles render markdown, user bubbles render plain
 * text, tool calls show the same specialised cards used in the live chat.
 *
 * Errors are split into two paths:
 *   - ``share.not_found`` / ``share.expired`` → friendly "expired or revoked"
 *     screen pointing back home.
 *   - everything else                          → generic "could not load"
 *     plus the technical code so users can quote it when reporting.
 */
export default function SharedSessionPage({
  params,
}: {
  params: Promise<{ token: string }>;
}) {
  const { token } = use(params);
  const t = useTranslations("chat.share");

  const [data, setData] = useState<PublicSharedSession | null>(null);
  const [error, setError] = useState<{ code: string; detail: string } | null>(
    null,
  );
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    fetchPublicShare(token)
      .then((d) => {
        if (!cancelled) setData(d);
      })
      .catch((err) => {
        if (cancelled) return;
        const code = (err as { code?: string }).code ?? "unknown";
        const detail = (err as Error).message ?? "Failed to load shared session";
        setError({ code, detail });
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [token]);

  if (loading) {
    return (
      <div className="flex h-screen items-center justify-center">
        <IconLoader2 className="size-6 animate-spin sh-muted" />
      </div>
    );
  }

  if (error) {
    const expired =
      error.code === "share.expired" || error.code === "share.not_found";
    return (
      <div className="flex h-screen flex-col items-center justify-center gap-4 p-8 text-center">
        <h1 className="text-lg font-semibold">
          {expired ? t("publicNotAvailable") : t("publicLoadFailed")}
        </h1>
        <p className="max-w-md text-sm sh-muted">
          {expired ? t("publicExpiredHint") : `${error.detail} (${error.code})`}
        </p>
        <Button asChild variant="outline">
          <Link href="/">
            <IconArrowLeft className="size-4" />
            {t("publicBackHome")}
          </Link>
        </Button>
      </div>
    );
  }

  if (!data) return null;

  return (
    <div className="mx-auto flex min-h-screen w-full max-w-3xl flex-col">
      <header className="border-b px-4 py-3">
        <div className="flex items-center justify-between gap-2">
          <h1 className="truncate text-base font-semibold">
            {data.title ?? t("publicUntitled")}
          </h1>
          <span className="inline-flex shrink-0 items-center gap-1 rounded-full bg-emerald-500/15 px-2 py-0.5 text-[10px] font-medium text-emerald-700 dark:text-emerald-400">
            <IconLockOpen className="size-3" />
            {t("publicReadonlyBadge")}
          </span>
        </div>
        <p className="mt-1 text-[11px] sh-muted">
          {t("publicSharedNote")}
          {data.expires_at && (
            <span className="ml-1">
              · {t("publicExpiresAt")}{" "}
              {new Date(data.expires_at).toLocaleString()}
            </span>
          )}
        </p>
      </header>

      <main className="flex-1 overflow-y-auto p-4 space-y-2">
        {data.messages.length === 0 && (
          <p className="text-center text-sm sh-muted">{t("publicEmpty")}</p>
        )}
        {data.messages.map((msg, idx) => {
          if (msg.role === "user") {
            const text =
              (msg.content_json as { text?: string })?.text ?? "";
            return (
              <div key={idx} className="flex justify-end gap-2 py-1">
                <div className="max-w-[85%] rounded-2xl rounded-tr-sm sh-primary px-3 py-2 text-sm break-words whitespace-pre-wrap">
                  {text}
                </div>
                <div className="flex size-7 shrink-0 items-center justify-center rounded-full sh-primary">
                  <IconUser className="size-3.5" />
                </div>
              </div>
            );
          }
          if (msg.role === "assistant") {
            const text =
              (msg.content_json as { text?: string })?.text ?? "";
            const events = Array.isArray(
              (msg.tool_call_json as { events?: unknown[] })?.events,
            )
              ? ((msg.tool_call_json as { events: unknown[] }).events as Array<
                  Record<string, unknown>
                >)
              : [];
            const calls = pairToolEvents(events);
            return (
              <div key={idx} className="space-y-1.5 py-1">
                <div className="flex items-start gap-2">
                  <div className="flex size-7 shrink-0 items-center justify-center rounded-full bg-[rgb(var(--color-primary))]/10 text-[rgb(var(--color-primary))]">
                    <IconRobot className="size-3.5" />
                  </div>
                  <div
                    className={cn(
                      "max-w-[85%] rounded-2xl rounded-tl-sm border sh-card px-3 py-2 text-sm break-words",
                      !text && "sh-muted italic",
                    )}
                  >
                    {text ? <Response>{text}</Response> : "(no text)"}
                  </div>
                </div>
                {calls.length > 0 && (
                  <div className="ml-9 space-y-1.5">
                    {calls.map((c) => (
                      <ToolCallCard
                        key={c.id}
                        toolCall={{
                          id: c.id,
                          name: c.name,
                          args: c.args,
                          result: c.result,
                          status: c.result !== undefined ? "completed" : "pending",
                        }}
                      />
                    ))}
                  </div>
                )}
              </div>
            );
          }
          return null;
        })}
      </main>

      <footer className="border-t p-3 text-center text-[11px] sh-muted">
        {t("publicFooter")}
      </footer>
    </div>
  );
}

function pairToolEvents(events: Array<Record<string, unknown>>) {
  const map = new Map<
    string,
    { id: string; name: string; args: Record<string, unknown>; result?: unknown }
  >();
  for (const ev of events) {
    if (typeof ev.id !== "string") continue;
    if (typeof ev.name === "string") {
      map.set(ev.id, {
        id: ev.id,
        name: ev.name,
        args:
          typeof ev.args === "object" && ev.args !== null
            ? (ev.args as Record<string, unknown>)
            : {},
      });
    } else if ("result" in ev) {
      const existing = map.get(ev.id);
      if (existing) existing.result = ev.result;
    }
  }
  return Array.from(map.values());
}
