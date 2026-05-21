"use client";

/**
 * Workspace governance — policies, budgets, usage events, tool-call logs
 * scoped to the active workspace (``scope=workspace`` + ``scope=agent``).
 *
 * Platform-level (``scope=global``) rules live on ``/admin/governance``.
 * The backend enforces the scope split — ``ws_svc.ensure_admin`` gates
 * non-admins out of the list + mutation endpoints.
 */

import { useState } from "react";
import {
    IconActivity,
    IconCash,
    IconShield,
    IconTool,
} from "@tabler/icons-react";
import { useTranslations } from "next-intl";

import { BudgetList } from "@/components/governance/BudgetList";
import { PolicyList } from "@/components/governance/PolicyList";
import { ToolCallLogsTable } from "@/components/governance/ToolCallLogsTable";
import { UsageEventsTable } from "@/components/governance/UsageEventsTable";
import { Button } from "@/components/ui/button";
import { PageHeader } from "@/components/ui/page-header";
import { cn } from "@/lib/utils";

type TabKey = "policies" | "budgets" | "usage" | "tools";

export default function WorkspaceGovernancePage() {
    const t = useTranslations("settings.governance");
    const [tab, setTab] = useState<TabKey>("policies");

    // `governance-page` is the stable testid consumed by the existing
    // governance-smoke e2e; `workspace-governance-page` disambiguates from
    // /admin/governance in case a future spec hits both.
    return (
        <div
            data-testid="governance-page"
            data-governance-variant="workspace"
        >
            <PageHeader
                title={t("title")}
                description={t("workspaceDescription")}
            />

            <div role="tablist" className="mb-4 flex gap-1 border-b">
                <TabButton
                    active={tab === "policies"}
                    onClick={() => setTab("policies")}
                    icon={<IconShield className="size-4" />}
                    label={t("nav.policies")}
                    testid="governance-tab-policies"
                />
                <TabButton
                    active={tab === "budgets"}
                    onClick={() => setTab("budgets")}
                    icon={<IconCash className="size-4" />}
                    label={t("nav.budgets")}
                    testid="governance-tab-budgets"
                />
                <TabButton
                    active={tab === "usage"}
                    onClick={() => setTab("usage")}
                    icon={<IconActivity className="size-4" />}
                    label={t("nav.usage")}
                    testid="governance-tab-usage"
                />
                <TabButton
                    active={tab === "tools"}
                    onClick={() => setTab("tools")}
                    icon={<IconTool className="size-4" />}
                    label={t("nav.tools")}
                    testid="governance-tab-tools"
                />
            </div>

            {tab === "policies" && (
                <PolicyList
                    allowedScopes={["workspace", "agent"]}
                    filterScopes={["workspace", "agent"]}
                />
            )}
            {tab === "budgets" && (
                <BudgetList
                    allowedScopes={["workspace", "agent"]}
                    filterScopes={["workspace", "agent"]}
                />
            )}
            {tab === "usage" && <UsageEventsTable />}
            {tab === "tools" && <ToolCallLogsTable />}
        </div>
    );
}

function TabButton({
    active,
    onClick,
    icon,
    label,
    testid,
}: {
    active: boolean;
    onClick: () => void;
    icon: React.ReactNode;
    label: string;
    testid: string;
}) {
    return (
        <Button
            type="button"
            variant="ghost"
            size="sm"
            role="tab"
            aria-selected={active}
            data-testid={testid}
            onClick={onClick}
            className={cn(
                "-mb-px rounded-none border-b-2",
                active
                    ? "border-current font-medium"
                    : "border-transparent sh-muted",
            )}
        >
            {icon}
            {label}
        </Button>
    );
}
