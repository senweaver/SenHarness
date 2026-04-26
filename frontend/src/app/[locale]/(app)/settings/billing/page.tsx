"use client";

import { Link } from "@/lib/navigation";
import {
  IconCoins,
  IconCreditCard,
  IconExternalLink,
  IconGift,
  IconSparkles,
} from "@tabler/icons-react";
import { useLocale, useTranslations } from "next-intl";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardFooter,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { PageHeader } from "@/components/ui/page-header";
import { useUsageReport } from "@/hooks/use-usage";

export default function BillingPage() {
  const t = useTranslations("settings.billing");
  const locale = useLocale();
  const { data: usage } = useUsageReport({ scope: "me" });

  const usd = (n: number) =>
    n >= 1 ? `$${n.toFixed(2)}` : `$${n.toFixed(4)}`;

  return (
    <div>
      <PageHeader title={t("title")} description={t("description")} />

      <div className="mb-3 grid gap-3 lg:grid-cols-3">
        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2 text-base">
              <IconSparkles className="size-4" />
              {t("plan")}
            </CardTitle>
            <CardDescription>{t("planDesc")}</CardDescription>
          </CardHeader>
          <CardContent>
            <div className="text-2xl font-semibold">{t("tierCommunity")}</div>
            <Badge variant="outline" className="mt-1">
              {t("selfHosted")}
            </Badge>
          </CardContent>
          <CardFooter>
            <Button asChild variant="outline" size="sm" className="w-full">
              <a href="https://github.com/senweaver" target="_blank" rel="noreferrer">
                {t("learnMore")}
                <IconExternalLink className="size-3.5" />
              </a>
            </Button>
          </CardFooter>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2 text-base">
              <IconCoins className="size-4" />
              {t("myUsage30d")}
            </CardTitle>
            <CardDescription>{t("myUsageDesc")}</CardDescription>
          </CardHeader>
          <CardContent className="space-y-1 text-sm">
            <Row
              label={t("cost")}
              value={usage ? usd(usage.summary.cost_usd) : "—"}
            />
            <Row
              label={t("tokens")}
              value={
                usage
                  ? (
                      usage.summary.input_tokens + usage.summary.output_tokens
                    ).toLocaleString(locale)
                  : "—"
              }
            />
            <Row
              label={t("turns")}
              value={usage ? usage.summary.turns.toLocaleString(locale) : "—"}
            />
            <Row
              label={t("sessions")}
              value={usage ? usage.summary.sessions.toLocaleString(locale) : "—"}
            />
          </CardContent>
          <CardFooter>
            <Button asChild variant="outline" size="sm" className="w-full">
              <Link href="/settings/usage">{t("openUsage")}</Link>
            </Button>
          </CardFooter>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2 text-base">
              <IconCreditCard className="size-4" />
              {t("billing")}
            </CardTitle>
            <CardDescription>{t("billingDesc")}</CardDescription>
          </CardHeader>
          <CardContent className="space-y-2">
            <p className="text-xs sh-muted">{t("noInvoice")}</p>
            <p className="text-xs sh-muted">{t("byoKey")}</p>
          </CardContent>
          <CardFooter>
            <Button asChild variant="outline" size="sm" className="w-full">
              <Link href="/settings/workspace/providers">
                {t("manageKeys")}
              </Link>
            </Button>
          </CardFooter>
        </Card>
      </div>

      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2 text-base">
            <IconGift className="size-4" />
            {t("roadmap")}
          </CardTitle>
          <CardDescription>{t("roadmapDesc")}</CardDescription>
        </CardHeader>
        <CardContent>
          <ul className="list-disc space-y-1 pl-5 text-sm sh-muted">
            <li>{t("roadmapItems.proTier")}</li>
            <li>{t("roadmapItems.budgetAlerts")}</li>
            <li>{t("roadmapItems.perAgentQuota")}</li>
            <li>{t("roadmapItems.invoice")}</li>
          </ul>
        </CardContent>
      </Card>
    </div>
  );
}

function Row({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex items-center justify-between">
      <span className="text-[11px] sh-muted">{label}</span>
      <span className="font-medium tabular-nums">{value}</span>
    </div>
  );
}
