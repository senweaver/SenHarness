import createMiddleware from "next-intl/middleware";
import { defaultLocale, locales } from "@/lib/i18n";

export default createMiddleware({
  locales: [...locales],
  defaultLocale,
  localePrefix: "as-needed",
  localeDetection: false,
});

export const config = {
  // Exclude only true static-asset suffixes (anchored with $) so that paths
  // containing a dot in a route segment — e.g. `/admin/settings/auth.registration`
  // — still go through the locale rewrite when the user is on the default
  // locale (no /en-US prefix). The previous `.*\..*` blanket exclusion
  // dropped any dotted path from middleware, which broke default-locale
  // settings deep-links.
  matcher: [
    "/((?!api|_next|.*\\.(?:png|jpg|jpeg|gif|svg|webp|ico|css|js|map|woff2?|ttf|otf|txt|xml|json)$).*)",
  ],
};
