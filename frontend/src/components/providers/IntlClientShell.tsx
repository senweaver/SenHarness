"use client";

import { IntlErrorCode, NextIntlClientProvider } from "next-intl";
import type { AbstractIntlMessages } from "next-intl";
import type { ReactNode } from "react";

import type { Locale } from "@/lib/i18n";

export function IntlClientShell({
  locale,
  messages,
  timeZone,
  children,
}: {
  locale: Locale;
  messages: AbstractIntlMessages;
  timeZone: string;
  children: ReactNode;
}) {
  return (
    <NextIntlClientProvider
      locale={locale}
      messages={messages}
      timeZone={timeZone}
      onError={(error) => {
        if (error.code === IntlErrorCode.MISSING_MESSAGE) return;
        console.error(error);
      }}
      getMessageFallback={({ error, key, namespace }) => {
        if (error.code === IntlErrorCode.MISSING_MESSAGE) return "";
        return namespace ? `${namespace}.${key}` : key;
      }}
    >
      {children}
    </NextIntlClientProvider>
  );
}
