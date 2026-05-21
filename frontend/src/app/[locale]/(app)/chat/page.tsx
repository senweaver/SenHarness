"use client";

import { Link } from "@/lib/navigation";
import { IconMessageCirclePlus } from "@tabler/icons-react";
import { useTranslations } from "next-intl";
import { Button } from "@/components/ui/button";

/**
 * `/chat` index — shown when no session is selected. Earlier the CTA
 * bounced the user back to the home composer (`/`), which dropped any
 * current agent context and was disorienting. The button now opens the
 * in-shell draft surface (`/chat/new`) so the session list and chat
 * column stay mounted side-by-side.
 */
export default function ChatIndexPage() {
  const t = useTranslations();
  return (
    <div className="flex h-full flex-1 items-center justify-center p-8">
      <div className="max-w-sm text-center">
        <IconMessageCirclePlus className="mx-auto size-8 sh-muted" />
        <p className="mt-3 text-sm sh-muted">{t("emptyStates.noSessions")}</p>
        <Button asChild size="sm" className="mt-3">
          <Link href="/chat/new">{t("chat.newSession")}</Link>
        </Button>
      </div>
    </div>
  );
}
