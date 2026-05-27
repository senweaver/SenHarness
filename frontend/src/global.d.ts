import type { locales } from "@/lib/i18n-config";

// Register the project's locale union with next-intl v4 so `useLocale()`,
// router helpers, and config types all narrow to ("zh-CN" | "en-US").
//
// We deliberately do NOT register `Messages` here. v4 supports strict
// message-key typing inferred from a sample dictionary, but the codebase
// uses dynamic keys (`t(`models.errors.${code}`)`) and passes `t` as
// a generic `(k: string) => string` callback in several places. Opting
// into strict keys is its own migration; until that work happens we
// keep the loose v3 behaviour for translators and tighten only locales.
declare module "next-intl" {
  interface AppConfig {
    Locale: (typeof locales)[number];
  }
}
