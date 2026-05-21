"use client";

import {
  IconBuildingCommunity,
} from "@tabler/icons-react";
import { useTranslations } from "next-intl";

import {
  DropdownMenuItem,
  DropdownMenuSub,
  DropdownMenuSubContent,
  DropdownMenuSubTrigger,
} from "@/components/ui/dropdown-menu";
import { switchActiveWorkspace } from "@/lib/workspace";
import { useWorkspaceStore } from "@/stores/workspace-store";

/**
 * Legacy SubMenu — kept for any callers that still mount the old
 * AvatarMenu workspace section. New code uses `WorkspaceSwitcherHeader`.
 */
export function WorkspaceSwitcherSubMenu() {
  const t = useTranslations("avatar");
  const workspaces = useWorkspaceStore((s) => s.workspaces);
  const active = useWorkspaceStore((s) => s.activeWorkspaceId);

  const onPick = async (id: string) => {
    await switchActiveWorkspace(id);
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

