"use client";

import { usePathname } from "@/lib/navigation";

import { AccountSettingsLayout } from "@/components/settings/AccountSettingsLayout";
import { SystemSettingsLayout } from "@/components/settings/SystemSettingsLayout";
import { WorkspaceSettingsLayout } from "@/components/settings/WorkspaceSettingsLayout";

const ACCOUNT_SEGMENTS = new Set([
  "profile",
  "appearance",
  "notifications",
  "shortcuts",
  "usage",
  "billing",
]);

const WORKSPACE_SEGMENTS = new Set([
  "workspace",
  "audit",
  "approvals",
  "moderation",
  "cross-platform",
  "secrets",
]);

function classify(pathname: string | null): "hub" | "account" | "workspace" | "system" {
  if (!pathname) return "hub";
  const match = pathname.match(/\/settings(?:\/([^/]+))?(?:\/.*)?$/);
  if (!match) return "hub";
  const first = match[1];
  if (!first) return "hub";
  if (first === "system") return "system";
  if (ACCOUNT_SEGMENTS.has(first)) return "account";
  if (WORKSPACE_SEGMENTS.has(first)) return "workspace";
  return "hub";
}

export default function SettingsLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  const pathname = usePathname();
  const scope = classify(pathname);

  if (scope === "account") {
    return <AccountSettingsLayout>{children}</AccountSettingsLayout>;
  }
  if (scope === "workspace") {
    return <WorkspaceSettingsLayout>{children}</WorkspaceSettingsLayout>;
  }
  if (scope === "system") {
    return <SystemSettingsLayout>{children}</SystemSettingsLayout>;
  }
  return <div className="flex flex-1 overflow-y-auto">{children}</div>;
}
