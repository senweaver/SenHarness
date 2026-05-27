"use client";

import { useMemo, useRef } from "react";
import { useSearchParams } from "next/navigation";
import { IconRobot } from "@tabler/icons-react";
import { useTranslations } from "next-intl";
import { toast } from "sonner";

import { useRouter } from "@/lib/navigation";
import { AgentAvatar } from "@/components/agents/AgentAvatar";
import {
  ChatInput,
  type ChatInputHandle,
  type ChatInputSubmission,
} from "@/components/chat/ChatInput";
import { useAgents } from "@/hooks/use-agents";
import { useCreateSession } from "@/hooks/use-create-session";
import { usePendingPromptStore } from "@/stores/pending-prompt-store";

/**
 * `/chat/new?agent=<id>` — DeepSeek-style **draft** surface.
 *
 * Earlier behaviour: the page fired `POST /sessions` on mount, so every
 * sidebar click on an agent leaked an "Untitled" empty session into the
 * recent list. New behaviour: render an empty conversation scaffold (header
 * + welcome card + composer); only the first user message creates the
 * backing session and forwards to `/chat/{sid}` (where `usePendingPromptStore`
 * forwards the message into the WS handshake).
 *
 * Squad sessions still go through the same flow via `?squad=<id>`.
 */
export default function NewChatRoute() {
  const router = useRouter();
  const search = useSearchParams();
  const create = useCreateSession();
  const t = useTranslations("chat");
  const setPending = usePendingPromptStore((s) => s.setPending);
  const { data: agents } = useAgents();
  const inputRef = useRef<ChatInputHandle>(null);

  const agentId = search.get("agent");
  const squadId = search.get("squad");
  const subjectId = squadId ?? agentId;
  const kind: "p2p" | "squad" = squadId ? "squad" : "p2p";

  const agent = useMemo(
    () => agents?.find((a) => a.id === agentId) ?? null,
    [agents, agentId],
  );

  const send = async ({ text, attachments, mode, model }: ChatInputSubmission) => {
    const trimmed = text.trim();
    if ((!trimmed && attachments.length === 0) || !subjectId) return;
    if (create.isPending) return;
    try {
      const session = await create.mutateAsync({
        kind,
        subject_id: subjectId,
        title: trimmed.slice(0, 48) || null,
      });
      setPending(session.id, {
        text: trimmed,
        attachments: attachments.length ? attachments : undefined,
        mode,
        model,
      });
      router.replace(`/chat/${session.id}`);
    } catch (err) {
      const code =
        (err as { code?: string; message?: string }).code ??
        (err as Error).message ??
        "create_session_failed";
      toast.error(t("createSessionFailed", { code }));
    }
  };

  const welcomeMessage = useMemo(() => {
    const meta = (agent?.metadata_json ?? {}) as { welcome_message?: unknown };
    return typeof meta.welcome_message === "string" && meta.welcome_message.trim()
      ? meta.welcome_message.trim()
      : null;
  }, [agent]);

  const recommendedQuestions = useMemo(() => {
    const meta = (agent?.metadata_json ?? {}) as {
      recommended_questions?: unknown;
    };
    if (!Array.isArray(meta.recommended_questions)) return [];
    return meta.recommended_questions
      .map((q) => (typeof q === "string" ? q.trim() : ""))
      .filter(Boolean)
      .slice(0, 6);
  }, [agent]);

  const pickRecommended = (question: string) => {
    void send({
      text: question,
      attachments: [],
      mode: inputRef.current?.getMode() ?? "flash",
      model: inputRef.current?.getModel() ?? null,
    });
  };

  return (
    <div className="flex h-full min-h-0 w-full flex-1 flex-col overflow-hidden">
      <div className="mx-auto flex min-h-0 w-full max-w-3xl flex-1 flex-col items-center justify-center gap-3 overflow-y-auto px-4 py-10 text-center">
        {agent ? (
          <>
            <AgentAvatar
              name={agent.name}
              avatarUrl={agent.avatar_url}
              className="size-14"
              fallbackClassName="text-xl"
            />
            <h2 className="text-lg font-medium">
              {t("draftEmpty.greeting", { name: agent.name })}
            </h2>
            {agent.description && (
              <p className="max-w-md text-sm sh-muted">{agent.description}</p>
            )}
            {welcomeMessage ? (
              <p className="max-w-md whitespace-pre-line text-sm">
                {welcomeMessage}
              </p>
            ) : null}
            <p className="text-xs sh-muted">{t("draftEmpty.hint")}</p>
            {recommendedQuestions.length > 0 ? (
              <div className="mt-2 flex w-full max-w-md flex-wrap justify-center gap-2">
                {recommendedQuestions.map((q) => (
                  <button
                    key={q}
                    type="button"
                    onClick={() => pickRecommended(q)}
                    disabled={create.isPending}
                    className="rounded-full border bg-card px-3 py-1 text-xs hover:bg-muted disabled:opacity-50"
                  >
                    {q}
                  </button>
                ))}
              </div>
            ) : null}
          </>
        ) : (
          <>
            <div className="flex size-14 items-center justify-center rounded-full bg-black/10 dark:bg-white/10">
              <IconRobot className="size-7 sh-muted" />
            </div>
            <p className="max-w-md text-sm sh-muted">
              {t("draftEmpty.pickAgent")}
            </p>
          </>
        )}
      </div>

      <div className="shrink-0">
        <ChatInput
          ref={inputRef}
          agentId={agentId}
          status={create.isPending ? "submitted" : "ready"}
          isConnected={Boolean(subjectId)}
          onSend={send}
        />
      </div>
    </div>
  );
}
