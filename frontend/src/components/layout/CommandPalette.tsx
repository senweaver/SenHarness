"use client";

import { Command } from "cmdk";
import { useEffect } from "react";
import { useRouter } from "@/lib/navigation";
import { useTranslations } from "next-intl";
import { IconHome, IconMessage, IconPuzzle, IconRobot, IconSearch, IconShoppingBag } from "@tabler/icons-react";
import { useCommandStore } from "@/stores/command-store";
import { useRecentAgents } from "@/hooks/use-agents";
import { cn } from "@/lib/utils";

export function CommandPalette() {
  const t = useTranslations("cmd");
  const nav = useTranslations("nav");
  const open = useCommandStore((s) => s.open);
  const setOpen = useCommandStore((s) => s.setOpen);
  const router = useRouter();
  const { data: agents } = useRecentAgents(20);

  useEffect(() => {
    const down = (e: KeyboardEvent) => {
      if (e.key === "k" && (e.metaKey || e.ctrlKey)) {
        e.preventDefault();
        setOpen(!open);
      } else if (e.key === "Escape") {
        setOpen(false);
      }
    };
    document.addEventListener("keydown", down);
    return () => document.removeEventListener("keydown", down);
  }, [open, setOpen]);

  if (!open) return null;

  const go = (href: string) => {
    setOpen(false);
    router.push(href);
  };

  return (
    <div className="fixed inset-0 z-50 flex items-start justify-center bg-black/40 p-4 pt-[15vh]">
      <Command
        className={cn(
          "w-full max-w-xl overflow-hidden rounded-lg border shadow-xl sh-card",
        )}
        label={t("placeholder")}
        onKeyDown={(e) => {
          if (e.key === "Escape") setOpen(false);
        }}
      >
        <div className="flex items-center border-b px-3">
          <IconSearch className="size-4 sh-muted" />
          <Command.Input
            placeholder={t("placeholder")}
            className="flex h-10 w-full bg-transparent px-3 text-sm outline-none"
          />
        </div>
        <Command.List className="max-h-[60vh] overflow-y-auto p-2">
          <Command.Empty className="px-3 py-2 text-xs sh-muted">—</Command.Empty>

          <Command.Group heading={t("sections.navigation")} className="px-1 py-1">
            <CmdItem onSelect={() => go("/")} icon={<IconHome className="size-4" />} label={nav("home")} />
            <CmdItem onSelect={() => go("/chat")} icon={<IconMessage className="size-4" />} label={nav("chat")} />
            <CmdItem onSelect={() => go("/settings/skills")} icon={<IconPuzzle className="size-4" />} label={nav("skills")} />
            <CmdItem onSelect={() => go("/marketplace")} icon={<IconShoppingBag className="size-4" />} label={nav("marketplace")} />
          </Command.Group>

          {agents && agents.length > 0 && (
            <Command.Group heading={t("sections.agents")} className="px-1 py-1">
              {agents.map((a) => (
                <CmdItem
                  key={a.id}
                  onSelect={() => go(`/chat/new?agent=${a.id}`)}
                  icon={<IconRobot className="size-4" />}
                  label={a.name}
                />
              ))}
            </Command.Group>
          )}
        </Command.List>
      </Command>
    </div>
  );
}

function CmdItem({
  icon,
  label,
  onSelect,
}: {
  icon: React.ReactNode;
  label: string;
  onSelect: () => void;
}) {
  return (
    <Command.Item
      onSelect={onSelect}
      className="flex cursor-default select-none items-center gap-2 rounded-md px-2 py-1.5 text-sm aria-selected:bg-black/5 dark:aria-selected:bg-white/10"
    >
      {icon}
      {label}
    </Command.Item>
  );
}
