"use client";

import { useState } from "react";
import { IconRefresh, IconBulb } from "@tabler/icons-react";
import { useTranslations } from "next-intl";
import { toast } from "sonner";
import { useRouter } from "@/lib/navigation";
import { useRecentAgents } from "@/hooks/use-agents";
import { useCreateSession } from "@/hooks/use-create-session";
import { usePendingPromptStore } from "@/stores/pending-prompt-store";

export function ExampleCards() {
  const t = useTranslations("home");
  const router = useRouter();
  const { data: recentAgents } = useRecentAgents(20);
  const setPending = usePendingPromptStore((s) => s.setPending);
  const create = useCreateSession();
  const [idx, setIdx] = useState(0);

  const rawExamples = t.raw("examples") as Array<
    Array<{ title: string; desc: string }>
  >;
  const pools: Array<{ title: string; desc: string }[]> = Array.isArray(rawExamples)
    ? rawExamples
    : [];
  const items = pools[idx % pools.length] ?? [];

  const agentId = recentAgents?.[0]?.id ?? null;

  const handleClick = async (prompt: string) => {
    if (create.isPending) return;
    try {
      const session = await create.mutateAsync({
        kind: "p2p",
        subject_id: agentId,
        title: prompt.slice(0, 48),
      });
      setPending(session.id, prompt);
      router.push(`/chat/${session.id}`);
    } catch (err) {
      const code = (err as { code?: string })?.code ?? "unknown";
      toast.error(t("sendFailed", { code }));
    }
  };

  return (
    <section className="mx-auto w-full max-w-3xl px-4 pb-12">
      <div className="mb-2 flex items-center justify-between">
        <div className="flex items-center gap-1 text-xs sh-muted">
          <IconBulb className="size-3.5" />
          {t("examplesTitle")}
        </div>
        <button
          onClick={() => setIdx((n) => n + 1)}
          className="inline-flex items-center gap-1 text-xs sh-muted hover:text-[rgb(var(--color-fg))]"
        >
          <IconRefresh className="size-3.5" />
          {t("refreshExamples")}
        </button>
      </div>

      <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
        {items.map((item) => (
          <button
            key={item.title}
            onClick={() => void handleClick(item.title)}
            disabled={create.isPending}
            className="rounded-lg border sh-card p-3 text-left transition-colors hover:bg-black/[0.02] disabled:opacity-60 dark:hover:bg-white/[0.03]"
          >
            <div className="text-sm font-medium">{item.title}</div>
            <div className="mt-1 text-xs sh-muted">{item.desc}</div>
          </button>
        ))}
      </div>
    </section>
  );
}
