export const locales = ["en-US", "zh-CN"] as const;
export type Locale = (typeof locales)[number];
export const defaultLocale: Locale = "en-US";

export function isLocale(value: string): value is Locale {
  return (locales as readonly string[]).includes(value);
}
