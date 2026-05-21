"use client";

import {
  Dialog,
  DialogContent,
  DialogTitle,
} from "@/components/ui/dialog";
import { HeroPrompt } from "./HeroPrompt";
import { useTranslations } from "next-intl";

interface NewChatDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
}

/**
 * `NewChatDialog` — floating compose dialog wrapping the existing
 * `HeroPrompt`. Plan §2 calls for HeroPrompt to be reachable via the
 * Dashboard's `[+ New Chat]` CTA rather than being embedded in the
 * page; this dialog hosts the same component without code-duplication.
 *
 * The dialog's submit handler routes to `/chat/[id]` like the
 * standalone HeroPrompt did, so picking an agent + sending the first
 * message works identically. We close the dialog on session create
 * via the auto-redirect inside HeroPrompt.
 */
export function NewChatDialog({ open, onOpenChange }: NewChatDialogProps) {
  const t = useTranslations("dashboard");
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-h-[85vh] overflow-y-auto p-0 sm:max-w-2xl">
        <DialogTitle className="sr-only">{t("newChatCta")}</DialogTitle>
        <HeroPrompt />
      </DialogContent>
    </Dialog>
  );
}
