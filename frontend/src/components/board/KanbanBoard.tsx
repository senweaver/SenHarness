"use client";

import {
  DndContext,
  type DragEndEvent,
  type DragStartEvent,
  KeyboardSensor,
  PointerSensor,
  closestCenter,
  useSensor,
  useSensors,
} from "@dnd-kit/core";
import {
  SortableContext,
  sortableKeyboardCoordinates,
  useSortable,
  verticalListSortingStrategy,
} from "@dnd-kit/sortable";
import { CSS } from "@dnd-kit/utilities";
import {
  IconArchive,
  IconCalendar,
  IconCheck,
  IconClock,
  IconPlus,
  IconRobot,
  IconUser,
} from "@tabler/icons-react";
import { useLocale, useTranslations } from "next-intl";
import { useCallback, useMemo, useState } from "react";
import { toast } from "sonner";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import {
  Dialog,
  DialogContent,
  DialogDescription,
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
import { Skeleton } from "@/components/ui/skeleton";
import { Textarea } from "@/components/ui/textarea";
import { useAgents } from "@/hooks/use-agents";
import {
  useArchiveCard,
  useBoard,
  useCompleteCard,
  useCreateCard,
  useMoveCard,
  useUpdateCard,
} from "@/hooks/use-project-board";
import { ApiError } from "@/lib/api";
import { relativeTime } from "@/lib/utils";
import {
  BOARD_COLUMN_ORDER,
  BOARD_PRIORITY_ORDER,
  type BoardCardColumnValue,
  type BoardCardPriorityValue,
  type BoardCardRead,
} from "@/types/api";

interface KanbanBoardProps {
  boardId: string;
}

interface NewCardDraft {
  title: string;
  description: string;
  priority: BoardCardPriorityValue;
  column: BoardCardColumnValue;
  assignee_agent_id: string | null;
  due_at: string;
}

const EMPTY_DRAFT: NewCardDraft = {
  title: "",
  description: "",
  priority: "normal",
  column: "backlog",
  assignee_agent_id: null,
  due_at: "",
};

const PRIORITY_BADGE: Record<
  BoardCardPriorityValue,
  "outline" | "default" | "warning" | "danger"
> = {
  low: "outline",
  normal: "default",
  high: "warning",
  urgent: "danger",
};

export function KanbanBoard({ boardId }: KanbanBoardProps) {
  const t = useTranslations("projectBoard");
  const locale = useLocale();

  const { data, isLoading, isError } = useBoard(boardId);
  const { data: agents } = useAgents();

  const moveCard = useMoveCard();
  const archiveCard = useArchiveCard();
  const completeCard = useCompleteCard();

  const [activeCardId, setActiveCardId] = useState<string | null>(null);
  const [drawerCardId, setDrawerCardId] = useState<string | null>(null);
  const [newCardOpen, setNewCardOpen] = useState(false);
  const [newCardDraft, setNewCardDraft] = useState<NewCardDraft>(EMPTY_DRAFT);

  const createCard = useCreateCard(boardId);

  const sensors = useSensors(
    useSensor(PointerSensor, { activationConstraint: { distance: 4 } }),
    useSensor(KeyboardSensor, {
      coordinateGetter: sortableKeyboardCoordinates,
    }),
  );

  const cardsById = useMemo(() => {
    const out: Record<string, BoardCardRead> = {};
    if (!data) return out;
    for (const col of BOARD_COLUMN_ORDER) {
      for (const c of data.columns[col] ?? []) {
        out[c.id] = c;
      }
    }
    return out;
  }, [data]);

  const drawerCard = drawerCardId ? cardsById[drawerCardId] ?? null : null;

  const onDragStart = useCallback((event: DragStartEvent) => {
    setActiveCardId(String(event.active.id));
  }, []);

  const onDragEnd = useCallback(
    async (event: DragEndEvent) => {
      const { active, over } = event;
      setActiveCardId(null);
      if (!over || !data) return;
      const overData = (over.data?.current ?? {}) as {
        column?: BoardCardColumnValue;
        cardId?: string;
      };
      const activeData = (active.data?.current ?? {}) as {
        column?: BoardCardColumnValue;
      };
      const sourceColumn = activeData.column;
      const targetColumn = overData.column;
      if (!sourceColumn || !targetColumn) return;

      const sourceList = data.columns[sourceColumn] ?? [];
      const targetList = data.columns[targetColumn] ?? [];
      const overCardId = overData.cardId;

      let targetPosition: number;
      if (sourceColumn === targetColumn) {
        const fromIndex = sourceList.findIndex((c) => c.id === active.id);
        const overIndex = overCardId
          ? sourceList.findIndex((c) => c.id === overCardId)
          : sourceList.length - 1;
        if (fromIndex === -1) return;
        targetPosition = overIndex === -1 ? sourceList.length - 1 : overIndex;
        if (fromIndex === targetPosition) return;
      } else {
        const overIndex = overCardId
          ? targetList.findIndex((c) => c.id === overCardId)
          : -1;
        targetPosition = overIndex === -1 ? targetList.length : overIndex;
      }

      try {
        await moveCard.mutateAsync({
          cardId: String(active.id),
          payload: {
            target_column: targetColumn,
            target_position: targetPosition,
          },
        });
      } catch (err) {
        toast.error(
          err instanceof ApiError ? err.message : t("toast.moveFailed"),
        );
      }
    },
    [data, moveCard, t],
  );

  const handleCreate = useCallback(async () => {
    if (!newCardDraft.title.trim()) {
      toast.error(t("toast.titleRequired"));
      return;
    }
    try {
      await createCard.mutateAsync({
        title: newCardDraft.title.trim(),
        description: newCardDraft.description.trim() || null,
        column: newCardDraft.column,
        priority: newCardDraft.priority,
        assignee_agent_id: newCardDraft.assignee_agent_id,
        due_at: newCardDraft.due_at
          ? new Date(newCardDraft.due_at).toISOString()
          : null,
      });
      toast.success(t("toast.cardCreated"));
      setNewCardOpen(false);
      setNewCardDraft(EMPTY_DRAFT);
    } catch (err) {
      toast.error(
        err instanceof ApiError ? err.message : t("toast.createFailed"),
      );
    }
  }, [createCard, newCardDraft, t]);

  if (isLoading) {
    return (
      <div className="grid gap-3 lg:grid-cols-4">
        {BOARD_COLUMN_ORDER.map((c) => (
          <Skeleton key={c} className="h-72" />
        ))}
      </div>
    );
  }

  if (isError || !data) {
    return (
      <div className="rounded-md border p-4 text-sm sh-muted">
        {t("loadError")}
      </div>
    );
  }

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-xl font-semibold">{data.board.name}</h1>
          {data.board.description && (
            <p className="text-sm sh-muted">{data.board.description}</p>
          )}
        </div>
        <Button size="sm" onClick={() => setNewCardOpen(true)}>
          <IconPlus className="size-4" />
          {t("newCardButton")}
        </Button>
      </div>

      <DndContext
        sensors={sensors}
        collisionDetection={closestCenter}
        onDragStart={onDragStart}
        onDragEnd={onDragEnd}
      >
        <div className="grid gap-3 lg:grid-cols-4">
          {BOARD_COLUMN_ORDER.map((col) => (
            <Column
              key={col}
              column={col}
              cards={data.columns[col] ?? []}
              onCardClick={setDrawerCardId}
              activeCardId={activeCardId}
            />
          ))}
        </div>
      </DndContext>

      {drawerCard && (
        <CardDrawer
          card={drawerCard}
          agents={(agents ?? []).map((a) => ({ id: a.id, name: a.name }))}
          onClose={() => setDrawerCardId(null)}
          onArchive={async () => {
            try {
              await archiveCard.mutateAsync(drawerCard.id);
              toast.success(t("toast.cardArchived"));
              setDrawerCardId(null);
            } catch (err) {
              toast.error(
                err instanceof ApiError ? err.message : t("toast.archiveFailed"),
              );
            }
          }}
          onComplete={async () => {
            try {
              await completeCard.mutateAsync(drawerCard.id);
              toast.success(t("toast.cardCompleted"));
            } catch (err) {
              toast.error(
                err instanceof ApiError ? err.message : t("toast.completeFailed"),
              );
            }
          }}
          locale={locale}
        />
      )}

      <NewCardDialog
        open={newCardOpen}
        onOpenChange={setNewCardOpen}
        draft={newCardDraft}
        setDraft={setNewCardDraft}
        agents={(agents ?? []).map((a) => ({ id: a.id, name: a.name }))}
        onSubmit={handleCreate}
        submitting={createCard.isPending}
      />

    </div>
  );
}

interface ColumnProps {
  column: BoardCardColumnValue;
  cards: BoardCardRead[];
  onCardClick: (id: string) => void;
  activeCardId: string | null;
}

function Column({ column, cards, onCardClick, activeCardId }: ColumnProps) {
  const t = useTranslations("projectBoard");
  return (
    <Card className="flex h-full flex-col">
      <CardHeader className="flex-row items-center justify-between border-b p-3">
        <CardTitle className="text-sm font-semibold">
          {t(`column.${column}`)}
        </CardTitle>
        <Badge variant="outline" className="text-[11px]">
          {cards.length}
        </Badge>
      </CardHeader>
      <CardContent className="flex flex-1 flex-col gap-2 p-2">
        <SortableContext
          id={column}
          items={cards.map((c) => c.id)}
          strategy={verticalListSortingStrategy}
        >
          {cards.length === 0 && (
            <DroppableEmptySlot column={column} />
          )}
          {cards.map((card) => (
            <CardItem
              key={card.id}
              card={card}
              column={column}
              onClick={() => onCardClick(card.id)}
              isDragging={activeCardId === card.id}
            />
          ))}
        </SortableContext>
      </CardContent>
    </Card>
  );
}

interface CardItemProps {
  card: BoardCardRead;
  column: BoardCardColumnValue;
  onClick: () => void;
  isDragging: boolean;
}

function CardItem({ card, column, onClick, isDragging }: CardItemProps) {
  const t = useTranslations("projectBoard");
  const locale = useLocale();
  const { data: agents } = useAgents();
  const { attributes, listeners, setNodeRef, transform, transition } =
    useSortable({
      id: card.id,
      data: { column, cardId: card.id },
    });

  const style: React.CSSProperties = {
    transform: CSS.Transform.toString(transform),
    transition,
    opacity: isDragging ? 0.4 : 1,
  };

  const agentName = card.assignee_agent_id
    ? (agents ?? []).find((a) => a.id === card.assignee_agent_id)?.name
    : null;

  const dueDate = card.due_at ? new Date(card.due_at) : null;
  const isOverdue = dueDate ? dueDate.getTime() < Date.now() && card.column !== "done" : false;

  return (
    <div
      ref={setNodeRef}
      style={style}
      {...attributes}
      {...listeners}
      onClick={onClick}
      onKeyDown={(e) => {
        if (e.key === "Enter" || e.key === " ") {
          e.preventDefault();
          onClick();
        }
      }}
      role="button"
      tabIndex={0}
      className="cursor-pointer rounded-md border bg-[rgb(var(--color-card))] p-2 text-sm shadow-sm transition-colors hover:bg-black/5 dark:hover:bg-white/5"
    >
      <div className="flex items-start justify-between gap-2">
        <span className="line-clamp-2 font-medium">{card.title}</span>
        <Badge
          variant={PRIORITY_BADGE[card.priority]}
          className="shrink-0 text-[10px]"
        >
          {t(`priority.${card.priority}`)}
        </Badge>
      </div>
      <div className="mt-2 flex flex-wrap items-center gap-2 text-[11px] sh-muted">
        {agentName && (
          <span className="inline-flex items-center gap-1">
            <IconRobot className="size-3" />
            {agentName}
          </span>
        )}
        {dueDate && (
          <span
            className={
              isOverdue
                ? "inline-flex items-center gap-1 text-red-600"
                : "inline-flex items-center gap-1"
            }
          >
            <IconCalendar className="size-3" />
            {relativeTime(dueDate.toISOString(), locale)}
          </span>
        )}
      </div>
    </div>
  );
}

interface DroppableEmptySlotProps {
  column: BoardCardColumnValue;
}

function DroppableEmptySlot({ column }: DroppableEmptySlotProps) {
  const t = useTranslations("projectBoard");
  // Empty placeholder sortable so dnd-kit registers the empty column as
  // a valid drop target.
  const { setNodeRef } = useSortable({
    id: `__empty__:${column}`,
    data: { column, cardId: undefined },
  });
  return (
    <div
      ref={setNodeRef}
      className="rounded-md border border-dashed p-3 text-center text-[11px] sh-muted"
    >
      {t("emptyColumn")}
    </div>
  );
}

interface CardDrawerProps {
  card: BoardCardRead;
  agents: Array<{ id: string; name: string }>;
  onClose: () => void;
  onArchive: () => void;
  onComplete: () => void;
  locale: string;
}

function CardDrawer({
  card,
  agents,
  onClose,
  onArchive,
  onComplete,
  locale,
}: CardDrawerProps) {
  const t = useTranslations("projectBoard");
  const updateCard = useUpdateCard(card.id);

  const [priority, setPriority] = useState<BoardCardPriorityValue>(card.priority);
  const [assigneeAgentId, setAssigneeAgentId] = useState<string | null>(
    card.assignee_agent_id,
  );

  const handleSavePriority = async (next: BoardCardPriorityValue) => {
    setPriority(next);
    try {
      await updateCard.mutateAsync({ priority: next });
    } catch (err) {
      toast.error(err instanceof ApiError ? err.message : t("toast.updateFailed"));
    }
  };

  const handleSaveAssignee = async (next: string) => {
    const value = next === "__none__" ? null : next;
    setAssigneeAgentId(value);
    try {
      await updateCard.mutateAsync({ assignee_agent_id: value });
    } catch (err) {
      toast.error(err instanceof ApiError ? err.message : t("toast.updateFailed"));
    }
  };

  return (
    <Dialog open onOpenChange={(open) => !open && onClose()}>
      <DialogContent className="max-w-xl">
        <DialogHeader>
          <DialogTitle>{card.title}</DialogTitle>
          <DialogDescription>
            {t("drawer.metaLine", {
              column: t(`column.${card.column}`),
              created: relativeTime(card.created_at, locale),
            })}
          </DialogDescription>
        </DialogHeader>

        <div className="space-y-3 text-sm">
          {card.description && (
            <p className="whitespace-pre-wrap rounded-md border bg-black/5 p-2 text-[13px] dark:bg-white/5">
              {card.description}
            </p>
          )}

          <div className="grid grid-cols-2 gap-3">
            <div className="space-y-1">
              <Label className="text-[11px] sh-muted">
                {t("drawer.priorityLabel")}
              </Label>
              <Select value={priority} onValueChange={(v) => handleSavePriority(v as BoardCardPriorityValue)}>
                <SelectTrigger>
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {BOARD_PRIORITY_ORDER.map((p) => (
                    <SelectItem key={p} value={p}>
                      {t(`priority.${p}`)}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>

            <div className="space-y-1">
              <Label className="text-[11px] sh-muted">
                {t("drawer.assigneeLabel")}
              </Label>
              <Select
                value={assigneeAgentId ?? "__none__"}
                onValueChange={handleSaveAssignee}
              >
                <SelectTrigger>
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="__none__">{t("drawer.noAssignee")}</SelectItem>
                  {agents.map((a) => (
                    <SelectItem key={a.id} value={a.id}>
                      {a.name}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
          </div>

          {card.due_at && (
            <div className="flex items-center gap-2 text-[12px] sh-muted">
              <IconClock className="size-3.5" />
              {t("drawer.dueLabel", {
                when: new Date(card.due_at).toLocaleString(locale),
              })}
            </div>
          )}

          {card.assignee_identity_id && (
            <div className="flex items-center gap-2 text-[12px] sh-muted">
              <IconUser className="size-3.5" />
              {t("drawer.identityAssigneeLabel")}
            </div>
          )}
        </div>

        <DialogFooter>
          <Button variant="outline" size="sm" onClick={onArchive}>
            <IconArchive className="size-4" />
            {t("drawer.archiveButton")}
          </Button>
          <Button size="sm" onClick={onComplete} disabled={card.column === "done"}>
            <IconCheck className="size-4" />
            {t("drawer.completeButton")}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

interface NewCardDialogProps {
  open: boolean;
  onOpenChange: (next: boolean) => void;
  draft: NewCardDraft;
  setDraft: (next: NewCardDraft) => void;
  agents: Array<{ id: string; name: string }>;
  onSubmit: () => void;
  submitting: boolean;
}

function NewCardDialog({
  open,
  onOpenChange,
  draft,
  setDraft,
  agents,
  onSubmit,
  submitting,
}: NewCardDialogProps) {
  const t = useTranslations("projectBoard");

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>{t("newCardDialog.title")}</DialogTitle>
        </DialogHeader>
        <div className="space-y-3 text-sm">
          <div className="space-y-1">
            <Label htmlFor="kanban-card-title">{t("newCardDialog.titleLabel")}</Label>
            <Input
              id="kanban-card-title"
              value={draft.title}
              onChange={(e) => setDraft({ ...draft, title: e.target.value })}
              placeholder={t("newCardDialog.titlePlaceholder")}
              autoFocus
            />
          </div>
          <div className="space-y-1">
            <Label htmlFor="kanban-card-desc">
              {t("newCardDialog.descriptionLabel")}
            </Label>
            <Textarea
              id="kanban-card-desc"
              value={draft.description}
              onChange={(e) => setDraft({ ...draft, description: e.target.value })}
              rows={3}
            />
          </div>
          <div className="grid grid-cols-2 gap-3">
            <div className="space-y-1">
              <Label>{t("newCardDialog.columnLabel")}</Label>
              <Select
                value={draft.column}
                onValueChange={(v) =>
                  setDraft({ ...draft, column: v as BoardCardColumnValue })
                }
              >
                <SelectTrigger>
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {BOARD_COLUMN_ORDER.map((c) => (
                    <SelectItem key={c} value={c}>
                      {t(`column.${c}`)}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
            <div className="space-y-1">
              <Label>{t("newCardDialog.priorityLabel")}</Label>
              <Select
                value={draft.priority}
                onValueChange={(v) =>
                  setDraft({ ...draft, priority: v as BoardCardPriorityValue })
                }
              >
                <SelectTrigger>
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {BOARD_PRIORITY_ORDER.map((p) => (
                    <SelectItem key={p} value={p}>
                      {t(`priority.${p}`)}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
          </div>
          <div className="grid grid-cols-2 gap-3">
            <div className="space-y-1">
              <Label>{t("newCardDialog.assigneeLabel")}</Label>
              <Select
                value={draft.assignee_agent_id ?? "__none__"}
                onValueChange={(v) =>
                  setDraft({
                    ...draft,
                    assignee_agent_id: v === "__none__" ? null : v,
                  })
                }
              >
                <SelectTrigger>
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="__none__">{t("drawer.noAssignee")}</SelectItem>
                  {agents.map((a) => (
                    <SelectItem key={a.id} value={a.id}>
                      {a.name}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
            <div className="space-y-1">
              <Label htmlFor="kanban-card-due">
                {t("newCardDialog.dueLabel")}
              </Label>
              <Input
                id="kanban-card-due"
                type="datetime-local"
                value={draft.due_at}
                onChange={(e) => setDraft({ ...draft, due_at: e.target.value })}
              />
            </div>
          </div>
        </div>
        <DialogFooter>
          <Button
            variant="outline"
            size="sm"
            onClick={() => onOpenChange(false)}
            disabled={submitting}
          >
            {t("newCardDialog.cancel")}
          </Button>
          <Button size="sm" onClick={onSubmit} disabled={submitting}>
            {t("newCardDialog.submit")}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
