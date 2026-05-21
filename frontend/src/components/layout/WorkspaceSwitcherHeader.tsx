"use client";

import { useMemo, useState } from "react";
import { Link, useRouter } from "@/lib/navigation";
import {
  IconCheck,
  IconChevronDown,
  IconChevronRight,
  IconLayoutSidebarLeftCollapse,
  IconPlus,
  IconSearch,
  IconSettings,
  IconUserPlus,
} from "@tabler/icons-react";
import { useTranslations } from "next-intl";
import { toast } from "sonner";

import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from "@/components/ui/popover";
import { Input } from "@/components/ui/input";
import { SimpleTooltip } from "@/components/ui/tooltip";
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip";
import { useAgentRuntimeSnapshot } from "@/hooks/use-agent-runtime";
import {
  useAgentRuntimeSummaries,
  useAgentRuntimeSummariesStream,
} from "@/hooks/use-agent-runtime-summaries";
import { switchActiveWorkspace } from "@/lib/workspace";
import { useWorkspaceStore } from "@/stores/workspace-store";
import { cn } from "@/lib/utils";
import type { WorkspaceRuntimeSummary } from "@/types/api";

import { CreateWorkspaceDialog } from "@/components/workspace/CreateWorkspaceDialog";
import { JoinWorkspaceDialog } from "@/components/workspace/JoinWorkspaceDialog";
import { WorkspaceStatusRow } from "./WorkspaceStatusRow";

interface WorkspaceSwitcherHeaderProps {
  collapsed: boolean;
  onToggleCollapsed: () => void;
}

export function WorkspaceSwitcherHeader({
  collapsed,
  onToggleCollapsed,
}: WorkspaceSwitcherHeaderProps) {
  const t = useTranslations("workspaceSwitcher");
  const tNav = useTranslations("nav");
  const router = useRouter();

  const workspaces = useWorkspaceStore((s) => s.workspaces);
  const activeId = useWorkspaceStore((s) => s.activeWorkspaceId);
  const active = useMemo(
    () => workspaces.find((w) => w.id === activeId) ?? null,
    [workspaces, activeId],
  );

  const { data: runtimeSnapshot } = useAgentRuntimeSnapshot();
  const attention =
    (runtimeSnapshot?.summary.stuck ?? 0) +
    (runtimeSnapshot?.summary.orphan ?? 0);
  const attentionLabel = t("attention", { count: attention });

  const { data: summariesData } = useAgentRuntimeSummaries();
  useAgentRuntimeSummariesStream();
  const summariesById = useMemo(() => {
    const map = new Map<string, WorkspaceRuntimeSummary>();
    for (const summary of summariesData?.summaries ?? []) {
      map.set(summary.workspace_id, summary);
    }
    return map;
  }, [summariesData]);

  const [open, setOpen] = useState(false);
  const [busyId, setBusyId] = useState<string | null>(null);
  const [query, setQuery] = useState("");
  const [createOpen, setCreateOpen] = useState(false);
  const [joinOpen, setJoinOpen] = useState(false);

  const filtered = useMemo(() => {
    if (!query.trim()) return workspaces;
    const q = query.toLowerCase();
    return workspaces.filter(
      (w) =>
        w.name.toLowerCase().includes(q) || w.slug.toLowerCase().includes(q),
    );
  }, [workspaces, query]);

  const initial = (active?.name ?? "?").slice(0, 1).toUpperCase();

  const onPick = async (workspaceId: string) => {
    if (workspaceId === activeId) {
      setOpen(false);
      return;
    }
    setBusyId(workspaceId);
    const ok = await switchActiveWorkspace(workspaceId);
    setBusyId(null);
    if (!ok) {
      toast.error(t("switching"));
      return;
    }
    setOpen(false);
    if (typeof window !== "undefined") {
      window.location.reload();
    }
  };

  const goWorkspaceSettings = () => {
    setOpen(false);
    router.push("/settings/workspace/branding");
  };

  const onExpandClick = (e: React.MouseEvent) => {
    e.stopPropagation();
    e.preventDefault();
    onToggleCollapsed();
  };

  return (
    <div
      className={cn(
        "flex w-full shrink-0 items-center border-b",
        collapsed ? "justify-center px-0" : "gap-1 pl-2 pr-1.5",
      )}
      style={{ height: "var(--sh-sidebar-logo-height)" }}
    >
      <Popover open={open} onOpenChange={setOpen}>
        {collapsed ? (
          <div className="relative size-9">
            <PopoverTrigger
              aria-label={t("label")}
              className="sh-nav-item flex size-9 items-center justify-center rounded-md sh-menu-text"
            >
              <span className="flex size-7 items-center justify-center rounded-md bg-[rgb(var(--color-primary)/0.12)] text-[12px] font-semibold text-[rgb(var(--color-primary))]">
                {initial}
              </span>
            </PopoverTrigger>
            {attention > 0 && (
              <span
                aria-label={attentionLabel}
                className="pointer-events-none absolute -top-0.5 -right-0.5 inline-flex h-4 min-w-[16px] items-center justify-center rounded-full bg-rose-500 px-1 text-[10px] font-bold tabular-nums leading-none text-white"
                data-testid="workspace-header-attention-pill"
              >
                {attention > 9 ? "9+" : attention}
              </span>
            )}
            <button
              type="button"
              aria-label={tNav("expand")}
              onClick={onExpandClick}
              onPointerDown={(e) => e.stopPropagation()}
              className="absolute -bottom-0.5 -right-0.5 z-10 flex size-5 items-center justify-center rounded-full bg-[rgb(var(--color-bg))] sh-menu-text shadow ring-1 ring-[rgb(var(--color-border))] transition-colors hover:bg-black/5 dark:hover:bg-white/10"
            >
              <IconChevronRight className="size-3" aria-hidden />
            </button>
          </div>
        ) : (
          <PopoverTrigger
            aria-label={t("label")}
            className="sh-nav-item flex h-9 min-w-0 flex-1 items-center gap-2 rounded-md px-2 text-[13px] sh-menu-text"
          >
            <span className="flex size-7 shrink-0 items-center justify-center rounded-md bg-[rgb(var(--color-primary)/0.12)] text-[12px] font-semibold text-[rgb(var(--color-primary))]">
              {initial}
            </span>
            <span className="min-w-0 flex-1 truncate text-left text-[14px] font-semibold leading-tight">
              {active?.name ?? "—"}
            </span>
            <IconChevronDown className="size-3.5 shrink-0 sh-muted" />
          </PopoverTrigger>
        )}
        <PopoverContent
          side="bottom"
          align="start"
          sideOffset={8}
          className="w-72 p-2"
        >
          <div className="mb-2 flex items-center justify-between px-1">
            <span className="text-[11px] font-semibold uppercase tracking-wide sh-muted">
              {t("switchTitle")}
            </span>
          </div>

          {workspaces.length >= 5 && (
            <div className="relative mb-2">
              <IconSearch className="absolute left-2 top-1/2 size-3.5 -translate-y-1/2 sh-muted" />
              <Input
                value={query}
                onChange={(e) => setQuery(e.target.value)}
                placeholder={t("searchPlaceholder")}
                className="h-8 pl-7 text-[12px]"
              />
            </div>
          )}

          <ul className="max-h-56 overflow-auto">
            {filtered.length === 0 ? (
              <li className="px-2 py-2 text-[12px] sh-muted">—</li>
            ) : (
              filtered.map((w) => (
                <li key={w.id}>
                  <button
                    type="button"
                    onClick={() => onPick(w.id)}
                    disabled={busyId === w.id}
                    className={cn(
                      "flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-left text-sm transition-colors hover:bg-black/5 dark:hover:bg-white/10",
                      busyId === w.id && "opacity-60",
                    )}
                  >
                    <span className="flex size-6 shrink-0 items-center justify-center rounded bg-black/5 text-[10px] font-semibold dark:bg-white/10">
                      {w.name.slice(0, 1).toUpperCase()}
                    </span>
                    <span className="flex min-w-0 flex-1 flex-col">
                      <span className="flex min-w-0 items-baseline gap-1">
                        <span className="truncate text-[13px]">{w.name}</span>
                        {w.role && (
                          <span className="shrink-0 text-[10px] sh-muted">
                            {w.role}
                          </span>
                        )}
                      </span>
                      <WorkspaceStatusRow
                        workspaceName={w.name}
                        summary={summariesById.get(w.id) ?? null}
                      />
                    </span>
                    {w.id === activeId && (
                      <IconCheck className="size-4 text-[rgb(var(--color-primary))]" />
                    )}
                  </button>
                </li>
              ))
            )}
          </ul>

          <div className="my-2 border-t" />

          <button
            type="button"
            onClick={goWorkspaceSettings}
            className="flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-[13px] hover:bg-black/5 dark:hover:bg-white/10"
          >
            <IconSettings className="size-4 sh-muted" />
            <span>{t("workspaceSettings")}</span>
          </button>

          <div className="my-2 border-t" />

          <button
            type="button"
            onClick={() => {
              setOpen(false);
              setCreateOpen(true);
            }}
            className="flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-[13px] hover:bg-black/5 dark:hover:bg-white/10"
          >
            <IconPlus className="size-4 sh-muted" />
            <span>{t("create")}</span>
          </button>
          <button
            type="button"
            onClick={() => {
              setOpen(false);
              setJoinOpen(true);
            }}
            className="flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-[13px] hover:bg-black/5 dark:hover:bg-white/10"
          >
            <IconUserPlus className="size-4 sh-muted" />
            <span>{t("join")}</span>
          </button>
        </PopoverContent>
      </Popover>

      {!collapsed && attention > 0 && (
        <SimpleTooltip label={attentionLabel} side="bottom">
          <Link
            href="/agent-view"
            aria-label={attentionLabel}
            data-testid="workspace-header-attention-pill"
            className="flex h-7 shrink-0 items-center gap-1 rounded-md px-2 text-[11px] font-medium text-rose-600 transition-colors hover:bg-rose-500/10 dark:text-rose-300"
          >
            <span
              aria-hidden
              className="size-1.5 rounded-full bg-rose-500"
            />
            <span className="tabular-nums">{attention}</span>
          </Link>
        </SimpleTooltip>
      )}

      {!collapsed && (
        <Tooltip>
          <TooltipTrigger asChild>
            <button
              type="button"
              onClick={onToggleCollapsed}
              aria-label={tNav("collapse")}
              className="flex size-8 shrink-0 items-center justify-center rounded-md sh-menu-text transition-colors hover:bg-black/5 dark:hover:bg-white/10"
            >
              <IconLayoutSidebarLeftCollapse className="size-4" />
            </button>
          </TooltipTrigger>
          <TooltipContent side="bottom">{tNav("collapse")}</TooltipContent>
        </Tooltip>
      )}

      <CreateWorkspaceDialog open={createOpen} onOpenChange={setCreateOpen} />
      <JoinWorkspaceDialog open={joinOpen} onOpenChange={setJoinOpen} />
    </div>
  );
}
