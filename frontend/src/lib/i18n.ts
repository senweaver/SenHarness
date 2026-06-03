import { cookies } from "next/headers";
import { notFound } from "next/navigation";
import { IntlErrorCode } from "next-intl";
import { getRequestConfig } from "next-intl/server";
import { unstable_cache } from "next/cache";
import { defaultLocale, isLocale, type Locale } from "@/lib/i18n-config";

const BOOTSTRAP_CACHE_KEY = ["public-bootstrap"];
const BOOTSTRAP_TTL_S = 300;

// Server-side fetches run inside the frontend container, where the public
// build-time URL (NEXT_PUBLIC_API_BASE_URL, e.g. localhost:8000) may be
// unreachable. Prefer the internal service URL when configured (same
// convention as lib/api.ts).
function serverApiBaseUrl(): string {
  const internal = process.env.SENHARNESS_INTERNAL_API_BASE_URL?.replace(
    /\/$/,
    "",
  );
  if (internal) return internal;
  return (process.env.NEXT_PUBLIC_API_BASE_URL || "").replace(/\/$/, "");
}

const fetchPlatformDefaultLocale = unstable_cache(
  async (): Promise<Locale> => {
    const baseUrl = serverApiBaseUrl();
    if (!baseUrl) return defaultLocale;
    try {
      const res = await fetch(`${baseUrl}/api/v1/public/bootstrap`, {
        next: { revalidate: BOOTSTRAP_TTL_S },
      });
      if (!res.ok) return defaultLocale;
      const body = (await res.json()) as { default_locale?: string };
      const candidate = body.default_locale ?? "";
      return isLocale(candidate) ? candidate : defaultLocale;
    } catch {
      return defaultLocale;
    }
  },
  BOOTSTRAP_CACHE_KEY,
  { revalidate: BOOTSTRAP_TTL_S, tags: BOOTSTRAP_CACHE_KEY },
);

async function fetchAuthedPreferredLocale(
  authToken: string,
): Promise<Locale | null> {
  const baseUrl = serverApiBaseUrl();
  if (!baseUrl) return null;
  try {
    const res = await fetch(`${baseUrl}/api/v1/me`, {
      headers: { Authorization: `Bearer ${authToken}` },
      cache: "no-store",
    });
    if (!res.ok) return null;
    const body = (await res.json()) as { preferred_locale?: string | null };
    const candidate = body.preferred_locale ?? "";
    return isLocale(candidate) ? candidate : null;
  } catch {
    return null;
  }
}

export async function resolveActiveLocale(
  requestedLocale: string | undefined | null,
): Promise<Locale> {
  // With `localePrefix: "as-needed"`, only NON-default locales get a URL
  // prefix (e.g. /zh-CN/...). Unprefixed paths always resolve to the static
  // `defaultLocale` via middleware, so a requested value equal to the static
  // default is implicit — fall through to cookie/profile/platform-default.
  // A requested non-default locale means the user explicitly chose it.
  if (
    requestedLocale &&
    isLocale(requestedLocale) &&
    requestedLocale !== defaultLocale
  ) {
    return requestedLocale;
  }
  const cookieStore = await cookies();
  const cookieLocale = cookieStore.get("NEXT_LOCALE")?.value;
  if (cookieLocale && isLocale(cookieLocale)) {
    return cookieLocale;
  }
  const authToken = cookieStore.get("access_token")?.value;
  if (authToken) {
    const fromMe = await fetchAuthedPreferredLocale(authToken);
    if (fromMe) return fromMe;
  }
  return await fetchPlatformDefaultLocale();
}

export default getRequestConfig(async ({ requestLocale }) => {
  const requested = await requestLocale;
  const resolved = await resolveActiveLocale(requested);
  if (!isLocale(resolved)) notFound();
  const messages = (await import(`../../messages/${resolved}.json`)).default;
  return {
    locale: resolved,
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
