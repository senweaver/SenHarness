"use client";

import { useMemo, useState } from "react";
import { IconRobot, IconSearch, IconUsersGroup } from "@tabler/icons-react";
import { useTranslations } from "next-intl";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { useAgents } from "@/hooks/use-agents";
import { useSquads } from "@/hooks/use-squads";
import {
  useSidebarItems,
  useTogglePin,
} from "@/hooks/use-sidebar-items";
import { cn } from "@/lib/utils";

interface AddPinDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
}

export function AddPinDialog({ open, onOpenChange }: AddPinDialogProps) {
  const t = useTranslations("sidebar.addPin");
  const tCommon = useTranslations("common");
  const { data: agents } = useAgents();
  const { data: squads } = useSquads();
  const { data: sidebar } = useSidebarItems();
  const toggle = useTogglePin();
  const [query, setQuery] = useState("");

  const starredIds = useMemo(() => {
    const set = new Set<string>();
    sidebar?.items.forEach((item) => set.add(item.id));
    return set;
  }, [sidebar]);

  const rows = useMemo(() => {
    const lowered = query.trim().toLowerCase();
    const all: Array<{
      type: "agent" | "squad";
      id: string;
      name: string;
      seed: string;
      starred: boolean;
    }> = [];
    agents?.forEach((agent) =>
      all.push({
        type: "agent",
        id: agent.id,
        name: agent.name,
        seed: agent.name.slice(0, 1).toUpperCase(),
        starred: starredIds.has(agent.id),
      }),
    );
    squads?.forEach((squad) =>
      all.push({
        type: "squad",
        id: squad.id,
        name: squad.name,
        seed: squad.name.slice(0, 1).toUpperCase(),
        starred: starredIds.has(squad.id),
      }),
    );
    if (!lowered) return all;
    return all.filter((row) => row.name.toLowerCase().includes(lowered));
  }, [agents, squads, query, starredIds]);

  const pin = (type: "agent" | "squad", id: string, name: string) => {
    toggle.mutate(
      { type, id, pinned: false },
      {
        onSuccess: () => {
          toast.success(t("added", { name }));
        },
      },
    );
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>{t("title")}</DialogTitle>
          <DialogDescription>{t("description")}</DialogDescription>
        </DialogHeader>

        <div className="relative">
          <IconSearch className="absolute left-2 top-1/2 size-3.5 -translate-y-1/2 sh-muted" />
          <Input
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder={t("searchPlaceholder")}
            className="pl-7"
            autoFocus
          />
        </div>

        <ul className="mt-3 max-h-72 overflow-y-auto">
          {rows.length === 0 ? (
            <li className="px-2 py-6 text-center text-[12px] sh-muted">
              {t("empty")}
            </li>
          ) : (
            rows.map((row) => (
              <li key={`${row.type}-${row.id}`}>
                <button
                  type="button"
                  disabled={row.starred}
                  onClick={() => pin(row.type, row.id, row.name)}
                  className={cn(
                    "flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-[13px] hover:bg-black/5 disabled:opacity-50 dark:hover:bg-white/10",
                  )}
                >
                  {row.type === "agent" ? (
                    <span className="flex size-6 shrink-0 items-center justify-center rounded-md bg-[rgb(var(--color-primary)/0.12)] text-[rgb(var(--color-primary))]">
                      <IconRobot className="size-3.5" />
                    </span>
                  ) : (
                    <span className="flex size-6 shrink-0 items-center justify-center rounded-md bg-black/5 dark:bg-white/10">
                      <IconUsersGroup className="size-3.5" />
                    </span>
                  )}
                  <span className="min-w-0 flex-1 truncate text-left">
                    {row.name}
                  </span>
                  {row.starred && (
                    <span className="text-[10px] sh-muted">
                      {t("alreadyAdded")}
                    </span>
                  )}
                </button>
              </li>
            ))
          )}
        </ul>

        <DialogFooter>
          <Button variant="ghost" onClick={() => onOpenChange(false)}>
            {tCommon("cancel")}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
