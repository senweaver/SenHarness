"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { Link, useRouter } from "@/lib/navigation";
import {
  IconCheck,
  IconChevronDown,
  IconLoader2,
  IconPaperclip,
  IconPinFilled,
  IconPlus,
  IconPuzzle,
  IconRobot,
  IconSearch,
  IconSend,
  IconWorld,
} from "@tabler/icons-react";
import { useTranslations } from "next-intl";
import { toast } from "sonner";

import { Textarea } from "@/components/ui/textarea";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { AttachmentView } from "@/components/chat/AttachmentView";
import { useMe } from "@/hooks/use-me";
import { useRecentAgents } from "@/hooks/use-agents";
import { useSkills } from "@/hooks/use-skills";
import { useUploadAttachment } from "@/hooks/use-attachments";
import { useCreateSession } from "@/hooks/use-create-session";
import { usePendingPromptStore } from "@/stores/pending-prompt-store";
import { useWorkspaceStore } from "@/stores/workspace-store";
import { useHomeComposeStore } from "@/stores/home-compose-store";
import { cn } from "@/lib/utils";

const DEFAULT_MAX_MB = 25;
const ALL_ACCEPT =
  "image/jpeg,image/png,image/gif,image/webp,audio/*,.pdf,.txt,.md,.csv,.json,.py,.js,.ts,.tsx,.html,.css,.yaml,.yml,.toml,.xml,.sql,.sh,.docx,.xlsx";

export function HeroPrompt() {
  const t = useTranslations("home");
  const tCommon = useTranslations("common");
  const router = useRouter();

  const { data: me } = useMe();
  const branding = useWorkspaceStore((s) =>
    s.workspaces.find((w) => w.id === s.activeWorkspaceId)?.branding,
  );
  const { data: recentAgents } = useRecentAgents(20);
  const { data: skills } = useSkills();
  const upload = useUploadAttachment(null);
  const setPending = usePendingPromptStore((s) => s.setPending);
  const create = useCreateSession();

  const draft = useHomeComposeStore((s) => s.draft);
  const setDraft = useHomeComposeStore((s) => s.setDraft);
  const agentId = useHomeComposeStore((s) => s.agentId);
  const setAgentId = useHomeComposeStore((s) => s.setAgentId);
  const attachments = useHomeComposeStore((s) => s.attachments);
  const addAttachment = useHomeComposeStore((s) => s.addAttachment);
  const removeAttachment = useHomeComposeStore((s) => s.removeAttachment);
  const webSearch = useHomeComposeStore((s) => s.webSearch);
  const toggleWebSearch = useHomeComposeStore((s) => s.toggleWebSearch);
  const starter = useHomeComposeStore((s) => s.starter);
  const setStarter = useHomeComposeStore((s) => s.setStarter);
  const reset = useHomeComposeStore((s) => s.reset);

  const fileInputRef = useRef<HTMLInputElement>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const [skillMenuOpen, setSkillMenuOpen] = useState(false);
  const [agentMenuOpen, setAgentMenuOpen] = useState(false);
  const [agentQuery, setAgentQuery] = useState("");

  const filteredAgents = useMemo(() => {
    const all = recentAgents ?? [];
    const q = agentQuery.trim().toLowerCase();
    if (!q) return all;
    return all.filter(
      (a) =>
        a.name.toLowerCase().includes(q) ||
        (a.description?.toLowerCase().includes(q) ?? false),
    );
  }, [recentAgents, agentQuery]);

  const groupedAgents = useMemo(() => {
    const pinned = filteredAgents.filter((a) => a.pinned);
    const recent = filteredAgents
      .filter((a) => !a.pinned && a.last_message_at)
      .slice(0, 5);
    const recentIds = new Set(recent.map((a) => a.id));
    const other = filteredAgents
      .filter((a) => !a.pinned && !recentIds.has(a.id))
      .slice(0, 5);
    return { pinned, recent, other };
  }, [filteredAgents]);

  const showAgentSearch = (recentAgents?.length ?? 0) >= 8;

  const maxMb =
    parseInt(process.env.NEXT_PUBLIC_MAX_UPLOAD_SIZE_MB ?? "", 10) ||
    DEFAULT_MAX_MB;

  // Default the active agent to the user's most-recent one once it loads.
  useEffect(() => {
    if (!agentId && recentAgents && recentAgents.length > 0) {
      setAgentId(recentAgents[0]!.id);
    }
  }, [recentAgents, agentId, setAgentId]);

  // Consume any starter pushed by QuickActions (Write/Image/Video chips).
  useEffect(() => {
    if (starter !== null) {
      setDraft(starter);
      setStarter(null);
      // Defer focus so the textarea exists in the DOM.
      requestAnimationFrame(() => {
        const el = textareaRef.current;
        if (!el) return;
        el.focus();
        el.setSelectionRange(starter.length, starter.length);
      });
    }
  }, [starter, setDraft, setStarter]);

  const displayName =
    me?.name ||
    (me?.email ? me.email.split("@")[0] : "") ||
    "";
  const welcome = branding?.welcome_h1
    ? branding.welcome_h1.replace("{name}", displayName)
    : displayName
      ? t("welcome", { name: displayName })
      : t("welcomeNoName");

  const submit = async () => {
    const content = draft.trim();
    if ((!content && attachments.length === 0) || create.isPending) return;
    try {
      const session = await create.mutateAsync({
        kind: "p2p",
        subject_id: agentId,
        title: content.slice(0, 48) || null,
      });
      setPending(session.id, {
        text: content,
        attachments: attachments.length ? [...attachments] : undefined,
        webSearch,
      });
      reset();
      router.push(`/chat/${session.id}`);
    } catch (err) {
      const code = (err as { code?: string })?.code ?? "unknown";
      toast.error(t("sendFailed", { code }));
    }
  };

  const onFilesChosen = async (files: FileList | null) => {
    if (!files || files.length === 0) return;
    for (const file of Array.from(files)) {
      if (file.size > maxMb * 1024 * 1024) {
        toast.error(t("uploadTooLarge", { name: file.name, mb: maxMb }));
        continue;
      }
      try {
        const att = await upload.mutateAsync(file);
        addAttachment({
          id: att.id,
          filename: att.filename,
          mime_type: att.mime_type,
          kind: att.kind,
          size_bytes: att.size_bytes,
        });
      } catch (err) {
        const msg = (err as Error).message ?? t("uploadFailed");
        toast.error(`${file.name}: ${msg}`);
      }
    }
    if (fileInputRef.current) fileInputRef.current.value = "";
  };

  const activeAgentName =
    (recentAgents ?? []).find((a) => a.id === agentId)?.name ??
    t("quickActions.newAgent");

  const canSend =
    !create.isPending &&
    !upload.isPending &&
    (draft.trim().length > 0 || attachments.length > 0);

  return (
    <section className="mx-auto flex w-full max-w-3xl flex-col items-center gap-8 px-4 py-12">
      <h1 className="text-center text-2xl font-semibold sm:text-3xl">{welcome}</h1>

      <div className="w-full rounded-xl border sh-card shadow-sm">
        {(attachments.length > 0 || upload.isPending) && (
          <div className="flex flex-wrap items-start gap-2 border-b px-3 py-2">
            {attachments.map((a) => (
              <AttachmentView
                key={a.id}
                att={a}
                compact
                onRemove={() => removeAttachment(a.id)}
              />
            ))}
            {upload.isPending && (
              <div className="flex h-20 w-20 items-center justify-center rounded-md border border-dashed">
                <IconLoader2 className="size-4 animate-spin sh-muted" />
              </div>
            )}
          </div>
        )}

        <Textarea
          ref={textareaRef}
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          placeholder={t("promptPlaceholder")}
          className="min-h-[96px] resize-none border-0 bg-transparent px-4 py-3 text-[15px] focus-visible:ring-0"
          onKeyDown={(e) => {
            if (e.key === "Enter" && !e.shiftKey && !e.ctrlKey) {
              e.preventDefault();
              void submit();
            }
          }}
        />

        <div className="flex items-center justify-between gap-2 border-t px-2 py-1.5">
          <div className="flex items-center gap-1 sh-muted">
            <input
              ref={fileInputRef}
              type="file"
              multiple
              accept={ALL_ACCEPT}
              className="hidden"
              onChange={(e) => void onFilesChosen(e.target.files)}
            />
            <Button
              type="button"
              variant="ghost"
              size="icon"
              aria-label={t("attach")}
              title={t("attach")}
              onClick={() => fileInputRef.current?.click()}
              disabled={upload.isPending}
            >
              {upload.isPending ? (
                <IconLoader2 className="size-4 animate-spin" />
              ) : (
                <IconPaperclip className="size-4" />
              )}
            </Button>

            <Button
              type="button"
              variant="ghost"
              size="icon"
              aria-label={webSearch ? t("webSearchOn") : t("webSearchOff")}
              title={webSearch ? t("webSearchOn") : t("webSearchOff")}
              onClick={toggleWebSearch}
              className={cn(
                webSearch &&
                  "bg-[rgb(var(--color-primary)/0.12)] text-[rgb(var(--color-primary))] hover:bg-[rgb(var(--color-primary)/0.18)]",
              )}
            >
              <IconWorld className="size-4" />
            </Button>

            <DropdownMenu open={skillMenuOpen} onOpenChange={setSkillMenuOpen}>
              <DropdownMenuTrigger asChild>
                <Button
                  type="button"
                  variant="ghost"
                  size="icon"
                  aria-label={t("skillsPicker")}
                  title={t("skillsPicker")}
                >
                  <IconPuzzle className="size-4" />
                </Button>
              </DropdownMenuTrigger>
              <DropdownMenuContent align="start" className="max-h-80 overflow-auto">
                <DropdownMenuLabel>{t("skillsPicker")}</DropdownMenuLabel>
                {(skills?.length ?? 0) === 0 && (
                  <div className="px-2 py-2 text-xs sh-muted">
                    {t("skillsEmpty")}
                  </div>
                )}
                {(skills ?? []).map((s) => (
                  <DropdownMenuItem
                    key={`${s.source}/${s.slug}`}
                    onSelect={() => {
                      const tag = `[#${s.slug}] `;
                      setDraft(draft.includes(tag) ? draft : `${tag}${draft}`);
                      setSkillMenuOpen(false);
                      requestAnimationFrame(() =>
                        textareaRef.current?.focus(),
                      );
                    }}
                  >
                    <span className="flex flex-1 flex-col">
                      <span className="text-sm">{s.name}</span>
                      <span className="line-clamp-1 text-[11px] sh-muted">
                        {s.description}
                      </span>
                    </span>
                    <span className="text-[10px] sh-muted">{s.source}</span>
                  </DropdownMenuItem>
                ))}
                <DropdownMenuSeparator />
                <DropdownMenuItem asChild>
                  <Link href="/skills" className="text-xs">
                    {t("skillsManage")}
                  </Link>
                </DropdownMenuItem>
              </DropdownMenuContent>
            </DropdownMenu>

            <DropdownMenu open={agentMenuOpen} onOpenChange={setAgentMenuOpen}>
              <DropdownMenuTrigger asChild>
                <button
                  type="button"
                  aria-label={t("agentPicker")}
                  className="ml-1 flex items-center gap-1 rounded-md bg-black/5 px-2 py-0.5 text-[11px] hover:bg-black/10 dark:bg-white/10 dark:hover:bg-white/15"
                >
                  <IconRobot className="size-3" />
                  <span className="max-w-[140px] truncate">
                    {activeAgentName}
                  </span>
                  <IconChevronDown className="size-3 sh-muted" />
                </button>
              </DropdownMenuTrigger>
              <DropdownMenuContent
                align="start"
                className="max-h-96 w-72 overflow-auto"
              >
                {showAgentSearch && (
                  <div className="relative px-1.5 py-1">
                    <IconSearch className="absolute left-3 top-1/2 size-3.5 -translate-y-1/2 sh-muted" />
                    <Input
                      value={agentQuery}
                      onChange={(e) => setAgentQuery(e.target.value)}
                      placeholder={tCommon("search")}
                      className="h-7 pl-7 text-[12px]"
                      autoFocus
                    />
                  </div>
                )}

                {(recentAgents?.length ?? 0) === 0 && (
                  <div className="px-2 py-2 text-xs sh-muted">
                    {tCommon("empty")}
                  </div>
                )}

                {groupedAgents.pinned.length > 0 && (
                  <>
                    <DropdownMenuLabel className="text-[10px] uppercase tracking-wide">
                      <span className="inline-flex items-center gap-1">
                        <IconPinFilled className="size-3 text-blue-500" />
                        Pinned
                      </span>
                    </DropdownMenuLabel>
                    {groupedAgents.pinned.map((a) => (
                      <AgentRow
                        key={`pin-${a.id}`}
                        agent={a}
                        active={agentId === a.id}
                        onPick={() => {
                          setAgentId(a.id);
                          setAgentMenuOpen(false);
                        }}
                      />
                    ))}
                  </>
                )}

                {groupedAgents.recent.length > 0 && (
                  <>
                    {groupedAgents.pinned.length > 0 && (
                      <DropdownMenuSeparator />
                    )}
                    <DropdownMenuLabel className="text-[10px] uppercase tracking-wide">
                      {t("agentRecent")}
                    </DropdownMenuLabel>
                    {groupedAgents.recent.map((a) => (
                      <AgentRow
                        key={`rec-${a.id}`}
                        agent={a}
                        active={agentId === a.id}
                        onPick={() => {
                          setAgentId(a.id);
                          setAgentMenuOpen(false);
                        }}
                      />
                    ))}
                  </>
                )}

                {groupedAgents.other.length > 0 && (
                  <>
                    {(groupedAgents.pinned.length > 0 ||
                      groupedAgents.recent.length > 0) && (
                      <DropdownMenuSeparator />
                    )}
                    {groupedAgents.other.map((a) => (
                      <AgentRow
                        key={`oth-${a.id}`}
                        agent={a}
                        active={agentId === a.id}
                        onPick={() => {
                          setAgentId(a.id);
                          setAgentMenuOpen(false);
                        }}
                      />
                    ))}
                  </>
                )}

                <DropdownMenuSeparator />
                <DropdownMenuItem asChild>
                  <Link href="/agents?new=1" className="text-xs">
                    <IconPlus className="size-3.5" />
                    {t("quickActions.newAgent")}
                  </Link>
                </DropdownMenuItem>
                <DropdownMenuItem asChild>
                  <Link href="/agents" className="text-xs">
                    {t("agentManage")}
                  </Link>
                </DropdownMenuItem>
              </DropdownMenuContent>
            </DropdownMenu>
          </div>

          <Button
            onClick={submit}
            size="icon"
            aria-label="send"
            title={t("send")}
            disabled={!canSend}
            data-testid="hero-send"
          >
            {create.isPending ? (
              <IconLoader2 className="size-4 animate-spin" />
            ) : (
              <IconSend className="size-4" />
            )}
          </Button>
        </div>
      </div>
    </section>
  );
}

function AgentRow({
  agent,
  active,
  onPick,
}: {
  agent: { id: string; name: string; description: string | null };
  active: boolean;
  onPick: () => void;
}) {
  return (
    <DropdownMenuItem onSelect={onPick}>
      <span className="flex flex-1 flex-col">
        <span className="text-sm">{agent.name}</span>
        {agent.description && (
          <span className="line-clamp-1 text-[11px] sh-muted">
            {agent.description}
          </span>
        )}
      </span>
      {active && (
        <IconCheck className="size-3.5 text-[rgb(var(--color-primary))]" />
      )}
    </DropdownMenuItem>
  );
}
