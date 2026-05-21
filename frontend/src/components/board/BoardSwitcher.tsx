"use client";

import { IconLayoutKanban, IconPlus } from "@tabler/icons-react";
import { useTranslations } from "next-intl";
import { useState } from "react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Textarea } from "@/components/ui/textarea";
import {
  useBoards,
  useCreateBoard,
} from "@/hooks/use-project-board";
import { ApiError } from "@/lib/api";

interface BoardSwitcherProps {
  squadId?: string | null;
  selectedBoardId: string | null;
  onSelect: (boardId: string) => void;
  isAdmin: boolean;
}

export function BoardSwitcher({
  squadId,
  selectedBoardId,
  onSelect,
  isAdmin,
}: BoardSwitcherProps) {
  const t = useTranslations("projectBoard");
  const { data: boards } = useBoards(squadId ?? undefined);
  const createBoard = useCreateBoard();

  const [open, setOpen] = useState(false);
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");

  const handleCreate = async () => {
    if (!name.trim()) {
      toast.error(t("toast.boardNameRequired"));
      return;
    }
    try {
      const board = await createBoard.mutateAsync({
        name: name.trim(),
        description: description.trim() || null,
        squad_id: squadId ?? null,
      });
      toast.success(t("toast.boardCreated"));
      setOpen(false);
      setName("");
      setDescription("");
      onSelect(board.id);
    } catch (err) {
      toast.error(
        err instanceof ApiError ? err.message : t("toast.createBoardFailed"),
      );
    }
  };

  return (
    <div className="flex items-center gap-2">
      <IconLayoutKanban className="size-4 sh-muted" />
      <Select
        value={selectedBoardId ?? ""}
        onValueChange={(v) => onSelect(v)}
        disabled={!boards || boards.length === 0}
      >
        <SelectTrigger className="w-64">
          <SelectValue placeholder={t("boardPickerPlaceholder")} />
        </SelectTrigger>
        <SelectContent>
          {(boards ?? []).map((b) => (
            <SelectItem key={b.id} value={b.id}>
              {b.name}
            </SelectItem>
          ))}
        </SelectContent>
      </Select>
      {isAdmin && (
        <Button size="sm" variant="outline" onClick={() => setOpen(true)}>
          <IconPlus className="size-4" />
          {t("newBoardButton")}
        </Button>
      )}

      <Dialog open={open} onOpenChange={setOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>{t("newBoardDialog.title")}</DialogTitle>
          </DialogHeader>
          <div className="space-y-3 text-sm">
            <div className="space-y-1">
              <Label htmlFor="kanban-board-name">
                {t("newBoardDialog.nameLabel")}
              </Label>
              <Input
                id="kanban-board-name"
                value={name}
                onChange={(e) => setName(e.target.value)}
                placeholder={t("newBoardDialog.namePlaceholder")}
                autoFocus
              />
            </div>
            <div className="space-y-1">
              <Label htmlFor="kanban-board-desc">
                {t("newBoardDialog.descriptionLabel")}
              </Label>
              <Textarea
                id="kanban-board-desc"
                value={description}
                onChange={(e) => setDescription(e.target.value)}
                rows={2}
              />
            </div>
          </div>
          <DialogFooter>
            <Button
              variant="outline"
              size="sm"
              onClick={() => setOpen(false)}
              disabled={createBoard.isPending}
            >
              {t("newBoardDialog.cancel")}
            </Button>
            <Button
              size="sm"
              onClick={handleCreate}
              disabled={createBoard.isPending}
            >
              {t("newBoardDialog.submit")}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
