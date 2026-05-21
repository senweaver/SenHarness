"use client";

import { Link } from "@/lib/navigation";
import { IconArrowRight, IconSparkles } from "@tabler/icons-react";
import { useTranslations } from "next-intl";

export function UpsellCard() {
  const t = useTranslations("dashboard");
  return (
    <section className="rounded-xl border border-dashed bg-gradient-to-br from-[rgb(var(--color-primary)/0.08)] to-transparent p-5">
      <div className="mb-2 flex items-center gap-2">
        <IconSparkles className="size-4 text-[rgb(var(--color-primary))]" />
        <h3 className="text-sm font-semibold">{t("upsellTitle")}</h3>
      </div>
      <p className="text-[12px] leading-5 sh-muted">{t("upsellBody")}</p>
      <Link
        href="/settings/billing"
        className="mt-3 inline-flex items-center gap-1 text-[12px] font-semibold text-[rgb(var(--color-primary))] hover:underline"
      >
        {t("upsellCta")}
        <IconArrowRight className="size-3.5" />
      </Link>
    </section>
  );
}
