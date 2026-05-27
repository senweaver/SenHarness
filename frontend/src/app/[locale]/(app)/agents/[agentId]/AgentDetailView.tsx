"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { Link, useRouter } from "@/lib/navigation";
import { useSearchParams } from "next/navigation";
import {
  IconArrowLeft,
  IconMessagePlus,
  IconPencil,
  IconStar,
  IconStarFilled,
  IconTrash,
} from "@tabler/icons-react";
import { AgentAvatar } from "@/components/agents/AgentAvatar";
import { useTranslations } from "next-intl";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { PageHeader } from "@/components/ui/page-header";
import { Skeleton } from "@/components/ui/skeleton";
import {
  AGENT_TAB_KEYS,
  AgentTabRail,
  type AgentTabKey,
} from "@/components/agents/AgentTabRail";
import { OverviewTab } from "@/components/agents/tabs/OverviewTab";
import { AbilitiesTab } from "@/components/agents/tabs/AbilitiesTab";
import { ChannelsTab } from "@/components/agents/tabs/ChannelsTab";
import { SchedulesTab } from "@/components/agents/tabs/SchedulesTab";
import { MemoryTab } from "@/components/agents/tabs/MemoryTab";
import { RulesTab } from "@/components/agents/tabs/RulesTab";
import { RunsTab } from "@/components/agents/tabs/RunsTab";
import {
  useAgent,
  useDeleteAgent,
  useIsAgentStarred,
  useToggleStar,
  useUpdateAgent,
} from "@/hooks/use-agent-mutations";

const DEFAULT_TAB: AgentTabKey = "overview";

function isValidTab(value: string | null): value is AgentTabKey {
  return Boolean(value) && AGENT_TAB_KEYS.includes(value as AgentTabKey);
}

interface AgentDetailViewProps {
  agentId: string;
}

export function AgentDetailView({ agentId }: AgentDetailViewProps) {
  const t = useTranslations("settings.agents.detail");
  const tHeader = useTranslations("agentDetail.header");
  const tCommon = useTranslations("common");
  const router = useRouter();
  const searchParams = useSearchParams();

  const tabParam = searchParams.get("tab");
  const initialTab = isValidTab(tabParam) ? tabParam : DEFAULT_TAB;

  const [activeTab, setActiveTab] = useState<AgentTabKey>(initialTab);

  useEffect(() => {
    if (isValidTab(tabParam) && tabParam !== activeTab) {
      setActiveTab(tabParam);
    }
  }, [tabParam, activeTab]);

  const { data: agent, isLoading, error } = useAgent(agentId);
  const { data: starredList } = useIsAgentStarred(agentId);
  const toggleStar = useToggleStar(agentId);
  const deleteAgent = useDeleteAgent();
  const [deleteOpen, setDeleteOpen] = useState(false);
  const starred = useMemo(
    () => Boolean((starredList ?? []).some((a) => a.id === agentId)),
    [starredList, agentId],
  );

  const onSelectTab = (next: AgentTabKey) => {
    setActiveTab(next);
    const params = new URLSearchParams(searchParams.toString());
    params.set("tab", next);
    router.replace(`/agents/${agentId}?${params.toString()}`);
  };

  const onToggleStar = async () => {
    try {
      await toggleStar.mutateAsync({ starred: !starred });
      toast.success(starred ? t("unstarred") : t("starred"));
    } catch {
      toast.error(t("starFailed"));
    }
  };

  const onConfirmDelete = async () => {
    try {
      await deleteAgent.mutateAsync(agentId);
      toast.success(tHeader("deleted"));
      setDeleteOpen(false);
      router.replace("/agents");
    } catch {
      toast.error(tHeader("deleteFailed"));
    }
  };

  if (isLoading) {
    return (
      <div className="flex h-full">
        <div className="sh-sidebar-surface w-[192px] shrink-0 p-2">
          {Array.from({ length: 7 }).map((_, i) => (
            <Skeleton key={i} className="mb-1 h-[44px]" />
          ))}
        </div>
        <div className="flex-1 p-6">
          <Skeleton className="mb-4 h-12" />
          <Skeleton className="h-64" />
        </div>
      </div>
    );
  }

  if (error || !agent) {
    return (
      <div className="p-6">
        <PageHeader
          title={t("notFoundTitle")}
          description={t("notFoundDesc")}
        />
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
    <div className="flex h-full min-h-0 flex-1">
      <AgentTabRail active={activeTab} onSelect={onSelectTab} />

      <section className="flex min-w-0 flex-1 flex-col overflow-y-auto">
        <header className="sticky top-0 z-10 border-b bg-[rgb(var(--color-bg))]/95 px-6 py-3 backdrop-blur">
          <div className="flex items-center gap-3">
            <AgentAvatar
              name={agent.name}
              avatarUrl={agent.avatar_url}
              className="size-9 rounded-lg"
              fallbackClassName="text-base rounded-lg"
            />
            <div className="min-w-0 flex-1">
              <EditableHeaderName
                agentId={agent.id}
                name={agent.name}
                ariaLabel={tHeader("name")}
                editLabel={tHeader("editName")}
                renameFailed={tHeader("renameFailed")}
                onlineLabel={tHeader("online")}
              />
              <p className="truncate text-[12px] sh-muted">
                {agent.description ?? t("noDescription")}
              </p>
            </div>
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
            <Button asChild size="sm">
              <Link href={`/chat/new?agent=${agent.id}`}>
                <IconMessagePlus className="size-4" />
                {tHeader("newChat")}
              </Link>
            </Button>
            <Button
              variant="ghost"
              size="icon"
              aria-label={tHeader("delete")}
              onClick={() => setDeleteOpen(true)}
              className="hover:text-red-600"
              data-testid="agent-detail-delete"
            >
              <IconTrash className="size-4" />
            </Button>
          </div>
          <Link
            href="/agents"
            className="mt-2 inline-flex items-center gap-1 text-[11px] sh-muted hover:text-[rgb(var(--color-fg))]"
          >
            <IconArrowLeft className="size-3" />
            {tCommon("back")}
          </Link>
        </header>

        <div className="flex-1 px-6 py-6">
          {activeTab === "overview" && <OverviewTab agent={agent} />}
          {activeTab === "abilities" && <AbilitiesTab agent={agent} />}
          {activeTab === "channels" && <ChannelsTab agentId={agentId} />}
          {activeTab === "schedules" && <SchedulesTab agentId={agentId} />}
          {activeTab === "memory" && <MemoryTab agentId={agentId} />}
          {activeTab === "rules" && <RulesTab agent={agent} />}
          {activeTab === "runs" && <RunsTab agentId={agentId} />}
        </div>
      </section>

      <Dialog
        open={deleteOpen}
        onOpenChange={(o) => {
          if (!o && !deleteAgent.isPending) setDeleteOpen(false);
        }}
      >
        <DialogContent className="sm:max-w-sm">
          <DialogHeader>
            <DialogTitle>{tHeader("deleteTitle")}</DialogTitle>
            <DialogDescription>
              {tHeader("deleteBody", { name: agent.name })}
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button
              variant="ghost"
              onClick={() => setDeleteOpen(false)}
              disabled={deleteAgent.isPending}
            >
              {tCommon("cancel")}
            </Button>
            <Button
              variant="destructive"
              onClick={() => void onConfirmDelete()}
              disabled={deleteAgent.isPending}
            >
              {tCommon("delete")}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}

interface EditableHeaderNameProps {
  agentId: string;
  name: string;
  ariaLabel: string;
  editLabel: string;
  renameFailed: string;
  onlineLabel: string;
}

function EditableHeaderName({
  agentId,
  name,
  ariaLabel,
  editLabel,
  renameFailed,
  onlineLabel,
}: EditableHeaderNameProps) {
  const update = useUpdateAgent(agentId);
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState("");
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (editing && inputRef.current) {
      inputRef.current.focus();
      inputRef.current.select();
    }
  }, [editing]);

  const startEditing = () => {
    setDraft(name);
    setEditing(true);
  };

  const commit = async () => {
    const next = draft.trim();
    if (!next || next === name) {
      setEditing(false);
      return;
    }
    try {
      await update.mutateAsync({ name: next });
      setEditing(false);
    } catch {
      toast.error(renameFailed);
      setEditing(false);
    }
  };

  const onKeyDown = (e: React.KeyboardEvent<HTMLInputElement>) => {
    if (e.key === "Enter") {
      e.preventDefault();
      void commit();
    } else if (e.key === "Escape") {
      e.preventDefault();
      setEditing(false);
    }
  };

  return (
    <div className="flex items-center gap-2">
      {editing ? (
        <input
          ref={inputRef}
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          onBlur={() => void commit()}
          onKeyDown={onKeyDown}
          aria-label={ariaLabel}
          disabled={update.isPending}
          className="min-w-0 flex-1 rounded-sm bg-black/5 px-1 text-base font-semibold tracking-tight outline-none focus:bg-black/10 dark:bg-white/5 dark:focus:bg-white/10"
        />
      ) : (
        <button
          type="button"
          onClick={startEditing}
          aria-label={editLabel}
          className="group inline-flex min-w-0 items-center gap-1.5 rounded-sm px-1 text-left text-base font-semibold tracking-tight outline-none hover:bg-black/5 focus-visible:bg-black/5 dark:hover:bg-white/5 dark:focus-visible:bg-white/5"
        >
          <span className="truncate">{name}</span>
          <IconPencil
            className="size-3.5 shrink-0 opacity-0 transition-opacity group-hover:opacity-60 group-focus-visible:opacity-60"
            aria-hidden
          />
        </button>
      )}
      <span className="inline-flex items-center gap-1 text-[11px] font-medium text-emerald-600">
        <span className="size-1.5 rounded-full bg-emerald-500" />
        {onlineLabel}
      </span>
    </div>
  );
}
