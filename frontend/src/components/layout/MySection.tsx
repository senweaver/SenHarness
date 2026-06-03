"use client";

import { useMemo, useState } from "react";
import { useSearchParams } from "next/navigation";
import { Link, usePathname } from "@/lib/navigation";
import {
  IconBook,
  IconBrain,
  IconBuildingStore,
  IconChevronRight,
  IconFilter,
  IconHierarchy,
  IconLayoutKanban,
  IconPlugConnected,
  IconPlus,
  IconRobot,
  IconSearch,
  IconShieldCheck,
  IconSparkles,
  IconStack2,
  IconUsersGroup,
} from "@tabler/icons-react";
import { useTranslations } from "next-intl";

import { Input } from "@/components/ui/input";
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip";
import { useSidebarItems } from "@/hooks/use-sidebar-items";
import { useSidebarStore } from "@/stores/sidebar-store";
import type { SidebarItem, SidebarItemType } from "@/types/api";
import { cn } from "@/lib/utils";

import { AddPinDialog } from "./AddPinDialog";
import { chatHref, MyItemRow } from "./MyItemRow";

function isItemActive(
  item: SidebarItem,
  pathname: string | null,
  agentParam: string | null,
  squadParam: string | null,
): boolean {
  const href = chatHref(item);
  if (!pathname) return false;
  if (item.type === "session") {
    return pathname === href || pathname.startsWith(href + "/");
  }
  const [hrefPath] = href.split("?");
  if (pathname !== hrefPath) return false;
  if (item.type === "agent") return agentParam === item.id;
  return squadParam === item.id;
}

const SEARCH_THRESHOLD = 12;
const FILTER_THRESHOLD = 6;

type FilterValue = SidebarItemType | "all";

const FILTER_OPTIONS: ReadonlyArray<{ value: FilterValue; key: string }> = [
  { value: "all", key: "filterAll" },
  { value: "agent", key: "filterAgents" },
  { value: "squad", key: "filterSquads" },
  { value: "session", key: "filterSessions" },
];

type WorkspaceToolLabelKey =
  | "agents"
  | "knowledge"
  | "flows"
  | "skills"
  | "memory"
  | "squads"
  | "batch"
  | "board"
  | "marketplace"
  | "channels"
  | "approvals";

interface WorkspaceToolDef {
  href: string;
  labelKey: WorkspaceToolLabelKey;
  icon: React.ReactNode;
}

const WORKSPACE_TOOLS: WorkspaceToolDef[] = [
  { href: "/agents", labelKey: "agents", icon: <IconRobot className="size-4 shrink-0" /> },
  { href: "/knowledge", labelKey: "knowledge", icon: <IconBook className="size-4 shrink-0" /> },
  { href: "/flows", labelKey: "flows", icon: <IconHierarchy className="size-4 shrink-0" /> },
  { href: "/skills", labelKey: "skills", icon: <IconSparkles className="size-4 shrink-0" /> },
  { href: "/memory", labelKey: "memory", icon: <IconBrain className="size-4 shrink-0" /> },
  { href: "/squads", labelKey: "squads", icon: <IconUsersGroup className="size-4 shrink-0" /> },
  { href: "/batch", labelKey: "batch", icon: <IconStack2 className="size-4 shrink-0" /> },
  { href: "/workspace/board", labelKey: "board", icon: <IconLayoutKanban className="size-4 shrink-0" /> },
  { href: "/marketplace", labelKey: "marketplace", icon: <IconBuildingStore className="size-4 shrink-0" /> },
  { href: "/channels", labelKey: "channels", icon: <IconPlugConnected className="size-4 shrink-0" /> },
  { href: "/approvals", labelKey: "approvals", icon: <IconShieldCheck className="size-4 shrink-0" /> },
];

interface MySectionProps {
  collapsed: boolean;
}

export function MySection({ collapsed }: MySectionProps) {
  const t = useTranslations("sidebar.my");
  const pathname = usePathname();
  const searchParams = useSearchParams();
  const agentParam = searchParams?.get("agent") ?? null;
  const squadParam = searchParams?.get("squad") ?? null;
  const { data, isLoading } = useSidebarItems();
  const mySectionOpen = useSidebarStore((s) => s.mySectionOpen);
  const toggleMySectionOpen = useSidebarStore((s) => s.toggleMySectionOpen);
  const workspaceSectionOpen = useSidebarStore((s) => s.workspaceSectionOpen);
  const toggleWorkspaceSectionOpen = useSidebarStore(
    (s) => s.toggleWorkspaceSectionOpen,
  );
  const [query, setQuery] = useState("");
  const [filter, setFilter] = useState<FilterValue>("all");
  const [filterOpen, setFilterOpen] = useState(false);
  const [addOpen, setAddOpen] = useState(false);

  const items = useMemo(() => data?.items ?? [], [data?.items]);
  const total = data?.total ?? items.length;

  const filtered = useMemo<SidebarItem[]>(() => {
    const byType =
      filter === "all" ? items : items.filter((item) => item.type === filter);
    if (!query.trim()) return byType;
    const lowered = query.toLowerCase();
    return byType.filter((item) =>
      item.name.toLowerCase().includes(lowered),
    );
  }, [items, query, filter]);

  const showSearch = total >= SEARCH_THRESHOLD;
  const showFilter = total >= FILTER_THRESHOLD;

  if (collapsed) {
    return (
      <div className="flex min-h-0 flex-1 flex-col overflow-y-auto sh-scroll-hidden">
        <nav className="mt-2 flex flex-col px-2">
          {items.map((item) => (
            <MyItemRow
              key={`${item.type}-${item.id}`}
              item={item}
              collapsed
              active={isItemActive(item, pathname, agentParam, squadParam)}
            />
          ))}
        </nav>
        <div className="my-2 mx-2 border-t" />
        <nav className="flex flex-col gap-1 px-2 pb-2">
          {WORKSPACE_TOOLS.map((tool) => (
            <CollapsedWorkspaceTool
              key={tool.href}
              tool={tool}
              pathname={pathname}
            />
          ))}
        </nav>
      </div>
    );
  }

  const myActions = mySectionOpen ? (
    <>
      {showFilter && (
        <Tooltip>
          <TooltipTrigger asChild>
            <button
              type="button"
              onClick={(e) => {
                e.stopPropagation();
                setFilterOpen((v) => !v);
              }}
              aria-pressed={filterOpen}
              aria-label={t("filter")}
              className={cn(
                "flex size-5 items-center justify-center rounded sh-muted hover:bg-black/10 dark:hover:bg-white/15",
                filterOpen &&
                  "bg-black/10 text-[rgb(var(--color-primary))] dark:bg-white/15",
              )}
            >
              <IconFilter className="size-3.5" />
            </button>
          </TooltipTrigger>
          <TooltipContent side="right">{t("filter")}</TooltipContent>
        </Tooltip>
      )}
      <Tooltip>
        <TooltipTrigger asChild>
          <button
            type="button"
            onClick={(e) => {
              e.stopPropagation();
              setAddOpen(true);
            }}
            aria-label={t("add.pin")}
            className="flex size-5 items-center justify-center rounded sh-muted hover:bg-black/10 dark:hover:bg-white/15"
          >
            <IconPlus className="size-3.5" />
          </button>
        </TooltipTrigger>
        <TooltipContent side="right">{t("add.pin")}</TooltipContent>
      </Tooltip>
    </>
  ) : null;

  return (
    <div className="mt-2 flex min-h-0 flex-1 flex-col">
      <SectionHeader
        open={mySectionOpen}
        label={t("sections.my")}
        controlsId="sidebar-my-section"
        onToggle={toggleMySectionOpen}
        actions={myActions}
      />

      {mySectionOpen && showFilter && filterOpen && (
        <div
          id="sidebar-my-section-filters"
          className="flex flex-wrap gap-1 px-2 pb-1.5"
        >
          {FILTER_OPTIONS.map((opt) => (
            <button
              key={opt.value}
              type="button"
              onClick={() => setFilter(opt.value)}
              aria-pressed={filter === opt.value}
              className={cn(
                "rounded-full px-2 py-0.5 text-[10px] font-medium transition-colors",
                filter === opt.value
                  ? "bg-[rgb(var(--color-primary))] text-white"
                  : "bg-black/5 sh-muted hover:bg-black/10 dark:bg-white/10 dark:hover:bg-white/15",
              )}
            >
              {t(opt.key)}
            </button>
          ))}
        </div>
      )}

      {mySectionOpen && showSearch && (
        <div className="relative px-2 pb-2">
          <IconSearch className="absolute left-4 top-1/2 size-3.5 -translate-y-1/2 sh-muted" />
          <Input
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder={t("searchPlaceholder")}
            className="h-7 pl-7 text-[12px]"
          />
        </div>
      )}

      {mySectionOpen && (
        <nav
          id="sidebar-my-section"
          className="flex min-h-0 flex-col overflow-y-auto px-2 pb-2 sh-scroll-hidden"
        >
          {isLoading && filtered.length === 0 && (
            <span className="px-3 py-2 text-[11px] sh-muted">{t("loading")}</span>
          )}
          {!isLoading && filtered.length === 0 && (
            <span className="px-3 py-2 text-[11px] sh-muted">
              {query.trim() || filter !== "all" ? t("noResults") : t("empty")}
            </span>
          )}
          {filtered.map((item) => (
            <MyItemRow
              key={`${item.type}-${item.id}`}
              item={item}
              collapsed={false}
              active={isItemActive(item, pathname, agentParam, squadParam)}
            />
          ))}
        </nav>
      )}

      <div className="my-2 mx-2 border-t" />

      <SectionHeader
        open={workspaceSectionOpen}
        label={t("sections.workspace")}
        controlsId="sidebar-workspace-section"
        onToggle={toggleWorkspaceSectionOpen}
      />

      {workspaceSectionOpen && (
        <WorkspaceToolsList pathname={pathname} />
      )}

      <AddPinDialog open={addOpen} onOpenChange={setAddOpen} />
    </div>
  );
}

interface SectionHeaderProps {
  open: boolean;
  label: string;
  controlsId: string;
  onToggle: () => void;
  actions?: React.ReactNode;
}

function SectionHeader({
  open,
  label,
  controlsId,
  onToggle,
  actions,
}: SectionHeaderProps) {
  const t = useTranslations("sidebar.my.sections");
  return (
    <div className="flex items-center gap-1 px-2 pb-1.5">
      <button
        type="button"
        onClick={onToggle}
        aria-expanded={open}
        aria-controls={controlsId}
        aria-label={open ? t("collapse") : t("expand")}
        className="group flex min-w-0 flex-1 items-center gap-1 rounded-md px-1 py-1 text-[11px] font-semibold uppercase tracking-wide sh-muted hover:bg-black/5 dark:hover:bg-white/10"
      >
        <IconChevronRight
          className={cn(
            "size-3.5 shrink-0 transition-transform",
            open && "rotate-90",
          )}
          aria-hidden
        />
        <span className="truncate text-left">{label}</span>
      </button>
      {actions ? (
        <div className="flex shrink-0 items-center gap-1">{actions}</div>
      ) : null}
    </div>
  );
}

function WorkspaceToolsList({ pathname }: { pathname: string | null }) {
  const t = useTranslations("nav");
  return (
    <nav
      id="sidebar-workspace-section"
      className="flex min-h-0 flex-col overflow-y-auto px-2 pb-2 sh-scroll-hidden"
    >
      {WORKSPACE_TOOLS.map((tool) => {
        const active =
          pathname === tool.href ||
          pathname?.startsWith(tool.href + "/") === true;
        return (
          <Link
            key={tool.href}
            href={tool.href}
            aria-current={active ? "page" : undefined}
            className={cn(
              "sh-nav-item flex h-8 shrink-0 items-center gap-2 rounded-md px-2 text-[13px]",
              active ? "sh-nav-active" : "sh-menu-text",
            )}
          >
            {tool.icon}
            <span className="truncate">{t(tool.labelKey)}</span>
          </Link>
        );
      })}
    </nav>
  );
}

function CollapsedWorkspaceTool({
  tool,
  pathname,
}: {
  tool: WorkspaceToolDef;
  pathname: string | null;
}) {
  const t = useTranslations("nav");
  const active =
    pathname === tool.href || pathname?.startsWith(tool.href + "/") === true;
  const label = t(tool.labelKey);
  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <Link
          href={tool.href}
          aria-label={label}
          aria-current={active ? "page" : undefined}
          className={cn(
            "sh-nav-item mx-auto flex h-9 w-9 items-center justify-center rounded-md",
            active ? "sh-nav-active" : "sh-menu-text",
          )}
        >
          {tool.icon}
        </Link>
      </TooltipTrigger>
      <TooltipContent side="right">{label}</TooltipContent>
    </Tooltip>
  );
}
