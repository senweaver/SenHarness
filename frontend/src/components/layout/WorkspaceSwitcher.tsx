"use client";

import { IconBuildingCommunity } from "@tabler/icons-react";
import { useTranslations } from "next-intl";
import {
  DropdownMenuItem,
  DropdownMenuSub,
  DropdownMenuSubContent,
  DropdownMenuSubTrigger,
} from "@/components/ui/dropdown-menu";
import { api } from "@/lib/api";
import { useAuthStore } from "@/stores/auth-store";
import { useWorkspaceStore } from "@/stores/workspace-store";

export function WorkspaceSwitcherSubMenu() {
  const t = useTranslations("avatar");
  const workspaces = useWorkspaceStore((s) => s.workspaces);
  const active = useWorkspaceStore((s) => s.activeWorkspaceId);
  const setActive = useWorkspaceStore((s) => s.setActive);

  const onPick = async (id: string) => {
    if (id === active) return;
    try {
      const data = await api.post<{ access_token: string }>(
        `/api/v1/workspaces/${id}/switch`,
      );
      // Access token carries `ws` claim — persist it + active workspace.
      useAuthStore.getState().setAccess(data.access_token, new Date(Date.now() + 30 * 60_000).toISOString());
      setActive(id);
    } catch {
      // swallow in P0; toast system lands with sonner wiring later
    }
  };

  return (
    <DropdownMenuSub>
      <DropdownMenuSubTrigger>
        <IconBuildingCommunity className="size-4" />
        <span>{t("switchWorkspace")}</span>
      </DropdownMenuSubTrigger>
      <DropdownMenuSubContent>
        {workspaces.length === 0 ? (
          <DropdownMenuItem disabled>—</DropdownMenuItem>
        ) : (
          workspaces.map((w) => (
            <DropdownMenuItem key={w.id} onClick={() => onPick(w.id)}>
              <span className="flex-1">{w.name}</span>
              {w.id === active && <span className="text-[11px] sh-muted">✓</span>}
            </DropdownMenuItem>
          ))
        )}
      </DropdownMenuSubContent>
    </DropdownMenuSub>
  );
}
