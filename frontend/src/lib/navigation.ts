/**
 * Locale-aware navigation helpers (next-intl v3).
 *
 * Always import Link, useRouter, usePathname, redirect from here
 * instead of "next/link" / "next/navigation" so that locale is
 * automatically preserved on every client-side transition.
 */
import { createNavigation } from "next-intl/navigation";
import { locales, defaultLocale } from "./i18n";

export const { Link, redirect, usePathname, useRouter, getPathname } =
  createNavigation({
    locales,
    defaultLocale,
    localePrefix: "as-needed",
  });
