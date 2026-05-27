import { getMessages, getTimeZone } from "next-intl/server";
import { notFound } from "next/navigation";
import { ThemeProvider } from "next-themes";

import { IntlClientShell } from "@/components/providers/IntlClientShell";
import { QueryProvider } from "@/components/providers/QueryProvider";
import { NextThemesScriptTagFilter } from "@/components/providers/NextThemesScriptTagFilter";
import { HtmlLangSync } from "@/components/providers/HtmlLangSync";
import { Toaster } from "@/components/ui/toast";
import { TooltipProvider } from "@/components/ui/tooltip";
import { isLocale, locales } from "@/lib/i18n-config";

export function generateStaticParams() {
  return locales.map((locale) => ({ locale }));
}

export default async function LocaleLayout({
  children,
  params,
}: {
  children: React.ReactNode;
  params: Promise<{ locale: string }>;
}) {
  const { locale } = await params;
  if (!isLocale(locale)) notFound();

  const messages = await getMessages();
  const timeZone = await getTimeZone();

  return (
    <>
      <HtmlLangSync locale={locale} />
      <NextThemesScriptTagFilter />
      <IntlClientShell locale={locale} messages={messages} timeZone={timeZone}>
        <ThemeProvider
          attribute="class"
          defaultTheme="system"
          enableSystem
          themes={["light", "dark", "soft", "system"]}
        >
          <QueryProvider>
            <TooltipProvider delayDuration={200} disableHoverableContent>
              {children}
              <Toaster />
            </TooltipProvider>
          </QueryProvider>
        </ThemeProvider>
      </IntlClientShell>
    </>
  );
}
