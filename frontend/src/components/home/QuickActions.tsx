"use client";

import { Link } from "@/lib/navigation";
import {
  IconFileText,
  IconPhoto,
  IconPlus,
  IconUsers,
  IconVideo,
} from "@tabler/icons-react";
import { useTranslations } from "next-intl";

import { useHomeComposeStore } from "@/stores/home-compose-store";

const chipStyle =
  "inline-flex items-center gap-1.5 rounded-full border sh-card px-3 py-1.5 text-xs sh-muted transition-colors hover:text-[rgb(var(--color-fg))]";

export function QuickActions() {
  const t = useTranslations("home.quickActions");
  const tStart = useTranslations("home.starters");
  const setStarter = useHomeComposeStore((s) => s.setStarter);

  const seed = (key: "writing" | "image" | "video") => () => {
    // Pushed into the home compose store; HeroPrompt will pick it up on
    // next render and focus the textarea.
    setStarter(tStart(key));
  };

  return (
    <div className="mx-auto flex w-full max-w-3xl flex-wrap items-center justify-center gap-2 px-4 pb-4">
      <Link href="/agents/new" className={chipStyle}>
        <IconPlus className="size-3.5" />
        {t("newAgent")}
      </Link>
      <Link href="/squads/new" className={chipStyle}>
        <IconUsers className="size-3.5" />
        {t("newSquad")}
      </Link>
      <button type="button" className={chipStyle} onClick={seed("writing")}>
        <IconFileText className="size-3.5" />
        {t("writing")}
      </button>
      <button type="button" className={chipStyle} onClick={seed("image")}>
        <IconPhoto className="size-3.5" />
        {t("image")}
      </button>
      <button type="button" className={chipStyle} onClick={seed("video")}>
        <IconVideo className="size-3.5" />
        {t("video")}
      </button>
    </div>
  );
}
