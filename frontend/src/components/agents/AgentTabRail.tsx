"use client";

import {
  IconAffiliate,
  IconBrain,
  IconChartLine,
  IconHistory,
  IconShield,
  IconSparkles,
  IconUserCircle,
} from "@tabler/icons-react";
import { useTranslations } from "next-intl";

import { cn } from "@/lib/utils";

export type AgentTabKey =
  | "overview"
  | "abilities"
  | "channels"
  | "schedules"
  | "memory"
  | "rules"
  | "runs";

interface TabDef {
  key: AgentTabKey;
  icon: React.ReactNode;
  labelKey: string;
}

const TABS: TabDef[] = [
  { key: "overview", icon: <IconUserCircle className="size-[18px]" />, labelKey: "overview" },
  { key: "abilities", icon: <IconSparkles className="size-[18px]" />, labelKey: "abilities" },
  { key: "channels", icon: <IconAffiliate className="size-[18px]" />, labelKey: "channels" },
  { key: "schedules", icon: <IconHistory className="size-[18px]" />, labelKey: "schedules" },
  { key: "memory", icon: <IconBrain className="size-[18px]" />, labelKey: "memory" },
  { key: "rules", icon: <IconShield className="size-[18px]" />, labelKey: "rules" },
  { key: "runs", icon: <IconChartLine className="size-[18px]" />, labelKey: "runs" },
];

export const AGENT_TAB_KEYS: AgentTabKey[] = TABS.map((tab) => tab.key);

interface AgentTabRailProps {
  active: AgentTabKey;
  onSelect: (tab: AgentTabKey) => void;
}

/**
 * Vertical tab rail for the agent detail page. Visually matches the
 * primary `SiderNav`: 192 px wide, 44 px row height, with a left
 * vertical bar on the active item.
 */
export function AgentTabRail({ active, onSelect }: AgentTabRailProps) {
  const t = useTranslations("agentDetail.tabs");
  return (
    <nav
      aria-label="agent tabs"
      className="sh-sidebar-surface flex w-[192px] shrink-0 flex-col p-2"
    >
      {TABS.map((tab) => {
        const isActive = tab.key === active;
        return (
          <button
            key={tab.key}
            type="button"
            onClick={() => onSelect(tab.key)}
            aria-current={isActive ? "page" : undefined}
            className={cn(
              "sh-nav-item relative flex h-[44px] items-center gap-3 rounded-md px-3 text-left text-[14px]",
              isActive ? "sh-nav-active" : "sh-menu-text",
            )}
          >
            <span className="shrink-0">{tab.icon}</span>
            <span className="flex-1 truncate">{t(tab.labelKey)}</span>
          </button>
        );
      })}
    </nav>
  );
}
