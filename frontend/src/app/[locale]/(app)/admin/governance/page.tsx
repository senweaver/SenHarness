"use client";

/**
 * Platform-level governance — only ``scope=global`` rules.
 *
 * Requires ``platform_admin``; ``AdminLayout`` bounces non-admins upstream
 * and the backend POST/PATCH/DELETE route checks ``require_platform_admin``
 * for the ``global`` branch independently.
 */

import { useState } from "react";
import { IconCash, IconShield } from "@tabler/icons-react";
import { useTranslations } from "next-intl";

import { BudgetList } from "@/components/governance/BudgetList";
import { PolicyList } from "@/components/governance/PolicyList";
import { Button } from "@/components/ui/button";
import { PageHeader } from "@/components/ui/page-header";
import { cn } from "@/lib/utils";

type TabKey = "policies" | "budgets";

export default function AdminGovernancePage() {
    const t = useTranslations("admin.governance");
    const tSettings = useTranslations("settings.governance");
    const [tab, setTab] = useState<TabKey>("policies");

    return (
        <div
            data-testid="governance-page"
            data-governance-variant="platform"
        >
            <PageHeader title={t("title")} description={t("description")} />

            <div role="tablist" className="mb-4 flex gap-1 border-b">
                <TabButton
                    active={tab === "policies"}
                    onClick={() => setTab("policies")}
                    icon={<IconShield className="size-4" />}
                    label={tSettings("nav.policies")}
                    testid="governance-tab-policies"
                />
                <TabButton
                    active={tab === "budgets"}
                    onClick={() => setTab("budgets")}
                    icon={<IconCash className="size-4" />}
                    label={tSettings("nav.budgets")}
                    testid="governance-tab-budgets"
                />
            </div>

            {tab === "policies" && (
                <PolicyList
                    allowedScopes={["global"]}
                    forceScope="global"
                    filterScopes={["global"]}
                />
            )}
            {tab === "budgets" && (
                <BudgetList
                    allowedScopes={["global"]}
                    forceScope="global"
                    filterScopes={["global"]}
                />
            )}
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
