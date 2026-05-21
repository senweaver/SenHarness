import { notFound } from "next/navigation";
import { IntlErrorCode } from "next-intl";
import { getRequestConfig } from "next-intl/server";

export const locales = ["en-US", "zh-CN"] as const;
export type Locale = (typeof locales)[number];
export const defaultLocale: Locale = "en-US";

export function isLocale(value: string): value is Locale {
  return (locales as readonly string[]).includes(value);
}

export default getRequestConfig(async ({ requestLocale }) => {
  const requested = (await requestLocale) ?? defaultLocale;
  if (!isLocale(requested)) notFound();
  const messages = (await import(`../../messages/${requested}.json`)).default;
  return {
    locale: requested,
    messages,
    timeZone: "UTC",
    onError(error) {
      if (error.code === IntlErrorCode.MISSING_MESSAGE) return;
      console.error(error);
    },
    getMessageFallback({ error, key, namespace }) {
      if (error.code === IntlErrorCode.MISSING_MESSAGE) return "";
      return namespace ? `${namespace}.${key}` : key;
    },
  };
});
