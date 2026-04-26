import { notFound } from "next/navigation";
import { getRequestConfig } from "next-intl/server";

export const locales = ["zh-CN", "en-US"] as const;
export type Locale = (typeof locales)[number];
export const defaultLocale: Locale = "zh-CN";

export function isLocale(value: string): value is Locale {
  return (locales as readonly string[]).includes(value);
}

export default getRequestConfig(async ({ requestLocale }) => {
  const requested = (await requestLocale) ?? defaultLocale;
  if (!isLocale(requested)) notFound();
  const messages = (await import(`../../messages/${requested}.json`)).default;
  return { locale: requested, messages };
});
