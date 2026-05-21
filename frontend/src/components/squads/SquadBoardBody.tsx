"use client";

import { useEffect, useState } from "react";
import { useTranslations } from "next-intl";

import { BoardSwitcher } from "@/components/board/BoardSwitcher";
import { KanbanBoard } from "@/components/board/KanbanBoard";
import { useBoards } from "@/hooks/use-project-board";
import { useSquad } from "@/hooks/use-squads";
import { useWorkspaceStore } from "@/stores/workspace-store";

export function SquadBoardBody({ squadId }: { squadId: string }) {
  const t = useTranslations("projectBoard");

  const { data: squad } = useSquad(squadId);
  const { data: boards } = useBoards(squadId);
  const activeWorkspace = useWorkspaceStore((s) =>
    s.workspaces.find((w) => w.id === s.activeWorkspaceId),
  );
  const isAdmin =
    activeWorkspace?.role === "owner" || activeWorkspace?.role === "admin";

  const [boardId, setBoardId] = useState<string | null>(null);

  useEffect(() => {
    if (!boardId && boards && boards.length > 0) {
      const first = boards[0];
      if (first) setBoardId(first.id);
    }
  }, [boards, boardId]);

  return (
    <div className="space-y-4">
      <header className="flex items-start justify-between gap-4">
        <div>
          <h2 className="text-base font-semibold">
            {t("squadPageTitle", { squad: squad?.name ?? "" })}
          </h2>
          <p className="text-sm sh-muted">{t("squadPageSubtitle")}</p>
        </div>
        <BoardSwitcher
          squadId={squadId}
          selectedBoardId={boardId}
          onSelect={setBoardId}
          isAdmin={isAdmin}
        />
      </header>

      {boardId ? (
        <KanbanBoard boardId={boardId} />
      ) : (
        <div className="rounded-md border p-6 text-center text-sm sh-muted">
          {t("noBoardForSquad")}
        </div>
      )}
    </div>
  );
}
