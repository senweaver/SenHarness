"use client";

import { useState } from "react";
import { IconBuildingCommunity, IconPlus, IconRefresh } from "@tabler/icons-react";
import { useTranslations } from "next-intl";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { CreateWorkspaceDialog } from "@/components/workspace/CreateWorkspaceDialog";
import { useActiveWorkspace } from "@/hooks/use-workspace";
import { useMe } from "@/hooks/use-me";
import { switchActiveWorkspace } from "@/lib/workspace";
import { useWorkspaceStore } from "@/stores/workspace-store";

interface WorkspaceRequiredGuardProps {
  children: React.ReactNode;
}

/**
 * Gates the authenticated shell on an active workspace. Workspace-scoped
 * routes 400 server-side when the caller's token has no ``ws`` claim
 * (platform admins created via ``create_platform_admin`` and accounts
 * that registered before personal-workspace provisioning was enabled),
 * so the shell stops here instead of bouncing the user into a broken
 * settings page.
 */
export function WorkspaceRequiredGuard({ children }: WorkspaceRequiredGuardProps) {
  const t = useTranslations("settings.workspace.guard");
  const activeId = useWorkspaceStore((s) => s.activeWorkspaceId);
  const { data: workspace, isLoading } = useActiveWorkspace();
  const { isLoading: meLoading } = useMe();
  const workspaces = useWorkspaceStore((s) => s.workspaces);
  const [createOpen, setCreateOpen] = useState(false);
  const [switching, setSwitching] = useState(false);

  if (activeId && (workspace || isLoading)) {
    return <>{children}</>;
  }
  // ``me`` is still in flight — render nothing so the empty-state CTA
  // doesn't flash for one frame before the persisted active workspace
  // is reconciled against the server membership list.
  if (meLoading) {
    return null;
  }

  const onPickFirstAvailable = async () => {
    const candidate = workspaces[0];
    if (!candidate) {
      toast.error(t("switchUnavailable"));
      return;
    }
    setSwitching(true);
    const ok = await switchActiveWorkspace(candidate.id);
    setSwitching(false);
    if (!ok) {
      toast.error(t("switchUnavailable"));
      return;
    }
    if (typeof window !== "undefined") window.location.reload();
  };

  return (
    <div className="flex flex-1 items-center justify-center p-6">
      <div className="w-full max-w-md space-y-4 rounded-lg border sh-card p-6 text-center">
        <div className="mx-auto flex size-12 items-center justify-center rounded-full bg-[rgb(var(--color-primary)/0.1)] text-[rgb(var(--color-primary))]">
          <IconBuildingCommunity className="size-6" />
        </div>
        <div className="space-y-1">
          <h2 className="text-base font-semibold">{t("title")}</h2>
          <p className="text-sm sh-muted">{t("description")}</p>
        </div>
        <div className="flex flex-col gap-2 sm:flex-row sm:justify-center">
          <Button onClick={() => setCreateOpen(true)} className="gap-1.5">
            <IconPlus className="size-4" />
            {t("createCta")}
          </Button>
          {workspaces.length > 0 ? (
            <Button
              variant="outline"
              onClick={() => void onPickFirstAvailable()}
              disabled={switching}
              className="gap-1.5"
            >
              <IconRefresh className="size-4" />
              {t("switchCta")}
            </Button>
          ) : null}
        </div>
      </div>
      <CreateWorkspaceDialog open={createOpen} onOpenChange={setCreateOpen} />
    </div>
  );
}
