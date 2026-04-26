import createMiddleware from "next-intl/middleware";
import { defaultLocale, locales } from "@/lib/i18n";

export default createMiddleware({
  locales: [...locales],
  defaultLocale,
  localePrefix: "as-needed",
});

export const config = {
  matcher: ["/((?!api|_next|.*\\..*).*)"],
};
