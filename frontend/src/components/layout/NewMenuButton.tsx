"use client";

import { useState } from "react";
import { useRouter } from "@/lib/navigation";
import {
  IconChevronDown,
  IconHierarchy,
  IconMessagePlus,
  IconPlus,
  IconUpload,
  IconUsersGroup,
} from "@tabler/icons-react";
import { useTranslations } from "next-intl";

import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip";
import { NewChatDialog } from "@/components/home/NewChatDialog";
import { cn } from "@/lib/utils";

interface NewMenuButtonProps {
  collapsed: boolean;
}

export function NewMenuButton({ collapsed }: NewMenuButtonProps) {
  const t = useTranslations("nav");
  const router = useRouter();
  const [menuOpen, setMenuOpen] = useState(false);
  const [chatOpen, setChatOpen] = useState(false);

  const openNewAgent = () => router.push("/agents?new=1");
  const openChat = () => setChatOpen(true);

  const items: Array<{
    key: string;
    label: string;
    icon: React.ReactNode;
    action: () => void;
  }> = [
    {
      key: "chat",
      label: t("newChat"),
      icon: <IconMessagePlus className="size-4" />,
      action: openChat,
    },
    {
      key: "squad",
      label: t("newSquad"),
      icon: <IconUsersGroup className="size-4" />,
      action: () => router.push("/squads/new"),
    },
    {
      key: "flow",
      label: t("newFlow"),
      icon: <IconHierarchy className="size-4" />,
      action: () => router.push("/flows/new"),
    },
    {
      key: "knowledge",
      label: t("uploadKnowledge"),
      icon: <IconUpload className="size-4" />,
      action: () => router.push("/knowledge"),
    },
  ];

  if (collapsed) {
    return (
      <>
        <DropdownMenu open={menuOpen} onOpenChange={setMenuOpen}>
          <Tooltip>
            <TooltipTrigger asChild>
              <DropdownMenuTrigger
                aria-label={t("newAgent")}
                className="mx-auto flex h-9 w-9 items-center justify-center rounded-md sh-primary shadow-sm transition-opacity hover:opacity-90 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-offset-1"
              >
                <IconPlus className="size-4" />
              </DropdownMenuTrigger>
            </TooltipTrigger>
            <TooltipContent side="right">{t("newAgent")}</TooltipContent>
          </Tooltip>
          <NewMenuItems items={items} onSelect={() => setMenuOpen(false)} />
        </DropdownMenu>
        <NewChatDialog open={chatOpen} onOpenChange={setChatOpen} />
      </>
    );
  }

  return (
    <>
      <div className="flex h-9 w-full overflow-hidden rounded-md sh-primary shadow-sm">
        <button
          type="button"
          onClick={openNewAgent}
          aria-label={t("newAgent")}
          className="flex flex-1 items-center justify-center gap-2 px-3 text-[13px] font-medium transition-opacity hover:opacity-90 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset"
        >
          <IconPlus className="size-4 shrink-0" />
          <span>{t("newAgent")}</span>
        </button>
        <div className="w-px bg-black/15 dark:bg-white/15" aria-hidden />
        <DropdownMenu open={menuOpen} onOpenChange={setMenuOpen}>
          <DropdownMenuTrigger
            aria-label={t("more")}
            className={cn(
              "flex w-8 items-center justify-center transition-opacity hover:opacity-90",
              "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset",
            )}
          >
            <IconChevronDown className="size-4" />
          </DropdownMenuTrigger>
          <NewMenuItems items={items} onSelect={() => setMenuOpen(false)} />
        </DropdownMenu>
      </div>
      <NewChatDialog open={chatOpen} onOpenChange={setChatOpen} />
    </>
  );
}

interface NewMenuItem {
  key: string;
  label: string;
  icon: React.ReactNode;
  action: () => void;
}

function NewMenuItems({
  items,
  onSelect,
}: {
  items: NewMenuItem[];
  onSelect: () => void;
}) {
  return (
    <DropdownMenuContent align="start" sideOffset={6} className="w-56">
      {items.map((item) => (
        <DropdownMenuItem
          key={item.key}
          onSelect={() => {
            item.action();
            onSelect();
          }}
        >
          {item.icon}
          <span>{item.label}</span>
        </DropdownMenuItem>
      ))}
    </DropdownMenuContent>
  );
}
