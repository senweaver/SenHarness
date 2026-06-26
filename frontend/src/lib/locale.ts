import type { Locale } from "@/lib/i18n-config";
import { getPathname } from "@/lib/navigation";

const NEXT_LOCALE_MAX_AGE = 60 * 60 * 24 * 365;

export function applyLocale(pathname: string, target: Locale) {
  document.cookie = `NEXT_LOCALE=${target}; Path=/; Max-Age=${NEXT_LOCALE_MAX_AGE}; SameSite=Lax`;
  window.location.href = getPathname({ href: pathname, locale: target });
}
