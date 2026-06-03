import createMiddleware from "next-intl/middleware";
import { defaultLocale, locales } from "@/lib/i18n-config";

// Middleware runs at the edge per request and cannot block on the
// async public-bootstrap fetch, so it always uses the static
// ``defaultLocale`` when no ``/<locale>/`` prefix is present. Platform
// and per-user locale resolution happens server-side in
// ``getRequestConfig`` (see ``lib/i18n.ts``).
//
// ``localeCookie: false`` stops the middleware from writing NEXT_LOCALE
// to the static default on every unprefixed request. That auto-write
// would otherwise pin the cookie to ``defaultLocale`` before the user
// makes any choice, masking the platform default and profile locale in
// ``resolveActiveLocale``. The navigation router (lib/navigation.ts)
// still writes NEXT_LOCALE on an explicit switch, so a deliberate choice
// of the default locale is preserved.
export default createMiddleware({
  locales: [...locales],
  defaultLocale,
  localePrefix: "as-needed",
  localeDetection: false,
  localeCookie: false,
});

export const config = {
  matcher: [
    "/((?!api|_next|.*\\.(?:png|jpg|jpeg|gif|svg|webp|ico|css|js|map|woff2?|ttf|otf|txt|xml|json)$).*)",
  ],
};
