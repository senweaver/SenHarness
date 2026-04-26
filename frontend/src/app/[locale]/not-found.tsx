"use client";

import { Link } from "@/lib/navigation";
import { IconHome, IconMessage, IconRoute2 } from "@tabler/icons-react";
import { useTranslations } from "next-intl";

import { Button } from "@/components/ui/button";

/**
 * Locale-aware 404 fallback. When a user lands on a path that has no
 * matching route (and is not already covered by the short-path aliases
 * declared in next.config.ts), show a friendly screen with a way back
 * instead of Next.js's bare-bones default page.
 */
export default function NotFound() {
  const t = useTranslations("notFound");

  return (
    <div className="flex min-h-screen items-center justify-center px-6">
      <div className="flex max-w-md flex-col items-center text-center">
        <div className="mb-4 flex size-14 items-center justify-center rounded-full bg-black/5 text-[rgb(var(--color-primary))] dark:bg-white/10">
          <IconRoute2 className="size-7" />
        </div>
        <h1 className="text-2xl font-semibold">{t("title")}</h1>
        <p className="mt-2 text-sm sh-muted">{t("description")}</p>

        <div className="mt-6 flex flex-wrap items-center justify-center gap-2">
          <Button asChild>
            <Link href="/">
              <IconHome className="size-4" />
              {t("backHome")}
            </Link>
          </Button>
          <Button asChild variant="outline">
            <Link href="/chat">
              <IconMessage className="size-4" />
              {t("openChat")}
            </Link>
          </Button>
        </div>

        <ul className="mt-8 grid grid-cols-2 gap-2 text-xs sh-muted">
          <li>
            <Link
              href="/agents"
              className="block rounded-md border sh-card px-3 py-2 hover:bg-black/5 dark:hover:bg-white/10"
            >
              {t("tiles.agents")}
            </Link>
          </li>
          <li>
            <Link
              href="/knowledge"
              className="block rounded-md border sh-card px-3 py-2 hover:bg-black/5 dark:hover:bg-white/10"
            >
              {t("tiles.knowledge")}
            </Link>
          </li>
          <li>
            <Link
              href="/settings/skills"
              className="block rounded-md border sh-card px-3 py-2 hover:bg-black/5 dark:hover:bg-white/10"
            >
              {t("tiles.skills")}
            </Link>
          </li>
          <li>
            <Link
              href="/settings/profile"
              className="block rounded-md border sh-card px-3 py-2 hover:bg-black/5 dark:hover:bg-white/10"
            >
              {t("tiles.settings")}
            </Link>
          </li>
        </ul>
      </div>
    </div>
  );
}
