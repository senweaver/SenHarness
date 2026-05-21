"use client";

import { useTranslations } from "next-intl";
import { IconAlertTriangle, IconArrowRight } from "@tabler/icons-react";
import { Link } from "@/lib/navigation";
import { useProviders } from "@/hooks/use-providers";

/**
 * Top-of-page hint pushing the user to Settings → Providers when their
 * workspace has no enabled LLM provider. Disappears silently once at least
 * one provider with a stored key is enabled.
 */
export function ProvidersOnboardingBanner() {
  const t = useTranslations("settings.providers.banner");
  const { data: providers, isLoading } = useProviders();

  if (isLoading) return null;

  const usable = (providers ?? []).some((p) => p.enabled && p.has_key);
  if (usable) return null;

  return (
    <div className="mb-4 flex items-center gap-3 rounded-md border border-amber-500/30 bg-amber-500/10 px-3 py-2 text-sm">
      <IconAlertTriangle className="size-4 text-amber-600 dark:text-amber-400 shrink-0" />
      <div className="flex-1">
        <p className="font-medium">{t("title")}</p>
        <p className="text-xs text-muted-foreground">{t("description")}</p>
      </div>
      <Link
        href="/settings/workspace/providers"
        className="inline-flex items-center gap-1 rounded-md bg-foreground px-2.5 py-1.5 text-xs font-medium text-background transition hover:opacity-90"
      >
        {t("cta")}
        <IconArrowRight className="size-3" />
      </Link>
    </div>
  );
}
