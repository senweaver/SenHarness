import createMiddleware from "next-intl/middleware";
import { defaultLocale, locales } from "@/lib/i18n-config";

// Middleware runs at the edge per request and cannot block on the
// async public-bootstrap fetch, so it always uses the static
// ``defaultLocale`` when no ``/<locale>/`` prefix is present. Platform
// and per-user locale resolution happens server-side in
// ``getRequestConfig`` (see ``lib/i18n.ts``).
export default createMiddleware({
  locales: [...locales],
  defaultLocale,
  localePrefix: "as-needed",
  localeDetection: false,
});

export const config = {
  matcher: [
    "/((?!api|_next|.*\\.(?:png|jpg|jpeg|gif|svg|webp|ico|css|js|map|woff2?|ttf|otf|txt|xml|json)$).*)",
  ],
};
