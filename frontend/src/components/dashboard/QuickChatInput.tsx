"use client";

import { useState } from "react";
import { Link, useRouter } from "@/lib/navigation";
import { IconLoader2, IconRobot, IconSend } from "@tabler/icons-react";
import { useTranslations } from "next-intl";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { useAgentTerm } from "@/components/nav/AgentTermLabel";
import { useRecentAgents } from "@/hooks/use-agents";
import { useCreateSession } from "@/hooks/use-create-session";
import { usePendingPromptStore } from "@/stores/pending-prompt-store";

export function QuickChatInput() {
  const t = useTranslations("dashboard.quickChat");
  const term = useAgentTerm();
  const router = useRouter();

  const { data: recentAgents, isLoading } = useRecentAgents(5);
  const defaultAgent = recentAgents?.[0] ?? null;

  const create = useCreateSession();
  const setPending = usePendingPromptStore((s) => s.setPending);

  const [draft, setDraft] = useState("");

  const noAgent = !isLoading && !defaultAgent;

  const submit = async () => {
    const content = draft.trim();
    if (!content || !defaultAgent || create.isPending) return;
    try {
      const session = await create.mutateAsync({
        kind: "p2p",
        subject_id: defaultAgent.id,
        title: content.slice(0, 48) || null,
      });
      setPending(session.id, { text: content });
      setDraft("");
      router.push(`/chat/${session.id}`);
    } catch (err) {
      const message = (err as Error).message ?? "unknown";
      toast.error(t("failed", { error: message }));
    }
  };

  if (noAgent) {
    return (
      <div className="flex items-center gap-3 rounded-lg border border-dashed bg-black/[0.02] px-3 py-2.5 text-[13px] dark:bg-white/[0.02]">
        <IconRobot className="size-4 shrink-0 sh-muted" aria-hidden />
        <span className="sh-muted">{t("noAgent")}</span>
        <Button asChild size="sm" variant="outline" className="ml-auto">
          <Link href="/agents?new=1">{t("noAgentCta")}</Link>
        </Button>
      </div>
    );
  }

  const canSend = !create.isPending && draft.trim().length > 0;

  return (
    <div className="flex items-end gap-2 rounded-lg border sh-card px-2 py-1.5 shadow-sm">
      <Textarea
        value={draft}
        onChange={(e) => setDraft(e.target.value)}
        placeholder={t("placeholder", { term })}
        rows={1}
        className="min-h-9 flex-1 resize-none border-0 bg-transparent px-2 py-1.5 text-[13px] focus-visible:ring-0"
        onKeyDown={(e) => {
          if (e.key === "Enter" && !e.shiftKey && !e.ctrlKey) {
            e.preventDefault();
            void submit();
          }
        }}
      />
      <Button
        type="button"
        size="icon"
        onClick={submit}
        disabled={!canSend}
        aria-label={t("send")}
        title={t("send")}
      >
        {create.isPending ? (
          <IconLoader2 className="size-4 animate-spin" />
        ) : (
          <IconSend className="size-4" />
        )}
      </Button>
    </div>
  );
}
