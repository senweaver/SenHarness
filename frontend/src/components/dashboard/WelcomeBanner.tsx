"use client";

import { useState } from "react";
import { Link } from "@/lib/navigation";
import {
  IconMessagePlus,
  IconRobot,
  IconUpload,
} from "@tabler/icons-react";
import { useTranslations } from "next-intl";

import { useMe } from "@/hooks/use-me";
import { useAgents } from "@/hooks/use-agents";
import { NewChatDialog } from "@/components/home/NewChatDialog";
import { QuickChatInput } from "@/components/dashboard/QuickChatInput";

interface WelcomeBannerProps {
  channelsCount?: number;
}

/**
 * Light bento welcome card.
 *
 * Surface = `sh-card` (white in light, slate-900 in dark). A single soft
 * primary blur orb sits in the top-right corner — subtle, not the dark
 * hero we shipped first. Three CTAs in priority order:
 *
 *   - **New chat session** (primary, opens compose dialog)
 *   - **New agent** (secondary, jumps to /agents?new=1)
 *   - **Upload knowledge** (tertiary, jumps to /knowledge)
 */
export function WelcomeBanner({ channelsCount = 0 }: WelcomeBannerProps) {
  const t = useTranslations("dashboard");
  const { data: me } = useMe();
  const { data: agents } = useAgents();

  const [composeOpen, setComposeOpen] = useState(false);

  const displayName =
    me?.name?.split(" ")[0] ||
    (me?.email ? me.email.split("@")[0] : "");

  const heading = displayName
    ? t("welcomeBack", { name: displayName })
    : t("welcomeBackNoName");

  const body = t("welcomeBody", {
    agents: agents?.length ?? 0,
    channels: channelsCount,
  });

  return (
    <>
      <section
        className="relative h-full overflow-hidden rounded-xl border sh-card p-6 sm:p-8"
        aria-label="welcome"
      >
        <div
          className="pointer-events-none absolute -right-20 -top-20 size-64 rounded-full bg-[rgb(var(--color-primary)/0.18)] blur-3xl"
          aria-hidden
        />
        <div
          className="pointer-events-none absolute -bottom-24 -left-12 size-48 rounded-full bg-[rgb(var(--color-primary)/0.08)] blur-3xl"
          aria-hidden
        />

        <div className="relative flex h-full flex-col gap-3">
          <h1 className="text-2xl font-semibold tracking-tight sm:text-[30px] sm:leading-[38px]">
            {heading}
          </h1>
          <p className="max-w-2xl text-sm leading-6 sh-muted">{body}</p>

          <div className="mt-4 max-w-2xl">
            <QuickChatInput />
          </div>

          <div className="mt-2 flex flex-wrap items-center gap-2">
            <button
              type="button"
              onClick={() => setComposeOpen(true)}
              className="inline-flex items-center gap-2 rounded-md sh-primary px-4 py-2 text-[13px] font-semibold shadow-sm transition-colors hover:opacity-90"
            >
              <IconMessagePlus className="size-4" />
              {t("newChatCta")}
            </button>
            <Link
              href="/agents?new=1"
              className="inline-flex items-center gap-2 rounded-md border bg-transparent px-4 py-2 text-[13px] font-medium shadow-sm transition-colors hover:bg-black/5 dark:hover:bg-white/10"
            >
              <IconRobot className="size-4" />
              {t("newAgentCta")}
            </Link>
            <Link
              href="/knowledge"
              className="inline-flex items-center gap-2 rounded-md border bg-transparent px-4 py-2 text-[13px] font-medium shadow-sm transition-colors hover:bg-black/5 dark:hover:bg-white/10"
            >
              <IconUpload className="size-4" />
              {t("uploadKnowledgeCta")}
            </Link>
          </div>
        </div>
      </section>

      <NewChatDialog open={composeOpen} onOpenChange={setComposeOpen} />
    </>
  );
}
