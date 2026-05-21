"use client";

import { useEffect, useState } from "react";
import { useTranslations } from "next-intl";

import { BoardSwitcher } from "@/components/board/BoardSwitcher";
import { KanbanBoard } from "@/components/board/KanbanBoard";
import { useBoards } from "@/hooks/use-project-board";
import { useWorkspaceStore } from "@/stores/workspace-store";

export default function WorkspaceBoardPage() {
  const t = useTranslations("projectBoard");

  const { data: boards } = useBoards();
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
    <div className="space-y-4 p-6">
      <header className="flex items-start justify-between gap-4">
        <div>
          <h1 className="text-lg font-semibold">{t("workspacePageTitle")}</h1>
          <p className="text-sm sh-muted">{t("workspacePageSubtitle")}</p>
        </div>
        <BoardSwitcher
          selectedBoardId={boardId}
          onSelect={setBoardId}
          isAdmin={isAdmin}
        />
      </header>

      {boardId ? (
        <KanbanBoard boardId={boardId} />
      ) : (
        <div className="rounded-md border p-6 text-center text-sm sh-muted">
          {isAdmin ? t("noBoardYetAdmin") : t("noBoardYetMember")}
        </div>
      )}
    </div>
  );
}
