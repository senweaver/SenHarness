import type { NextConfig } from "next";
import createNextIntlPlugin from "next-intl/plugin";

const withNextIntl = createNextIntlPlugin("./src/lib/i18n.ts");

/**
 * Short-path aliases. Many product entries live under nested paths
 * (e.g. /settings/skills, /admin/users) but users naturally type the
 * shorter form (e.g. /skills, /users) into the address bar. Without
 * an alias they get a hard 404. Each entry below produces redirects
 * for both the bare path and the locale-prefixed variants so the
 * fallback works for `localePrefix: "as-needed"` users.
 */
const SHORT_PATH_ALIASES: Array<[string, string]> = [
  ["skills", "/settings/skills"],
  ["memory", "/settings/memory"],
  ["audit", "/settings/audit"],
  ["usage", "/settings/usage"],
  ["secrets", "/settings/secrets"],
  ["channels", "/settings/channels"],
  ["moderation", "/settings/moderation"],
  ["billing", "/settings/billing"],
  ["profile", "/settings/profile"],
  ["soul", "/settings/profile/soul"],
  ["appearance", "/settings/appearance"],
  ["shortcuts", "/settings/shortcuts"],
  ["governance", "/settings/workspace/governance"],
  ["branding", "/settings/workspace/branding"],
  ["members", "/settings/workspace/members"],
  ["departments", "/settings/workspace/departments"],
  ["providers", "/settings/workspace/providers"],
  ["runtimes", "/settings/workspace/runtimes"],
  ["users", "/admin/users"],
  ["workspaces", "/admin/workspaces"],
  ["keyring", "/admin/keyring"],
  ["observability", "/admin/observability"],
];

const nextConfig: NextConfig = {
  output: "standalone",
  reactStrictMode: true,
  experimental: {
    // typedRoutes: true,
  },
  async redirects() {
    const out: Array<{ source: string; destination: string; permanent: boolean }> = [];
    for (const [from, to] of SHORT_PATH_ALIASES) {
      // Default-locale path (no locale prefix in URL because next-intl
      // is configured with `localePrefix: "as-needed"`).
      out.push({
        source: `/${from}`,
        destination: to,
        permanent: false,
      });
      // Explicit locale prefix: /en-US/skills, /zh-CN/skills, …
      out.push({
        source: `/:locale(zh-CN|en-US)/${from}`,
        destination: `/:locale${to}`,
        permanent: false,
      });
    }
    return out;
  },
  async rewrites() {
    const apiBase = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000";
    return [
      // Primary proxy used by the browser bundle.
      {
        source: "/api/backend/:path*",
        destination: `${apiBase}/api/:path*`,
      },
      // Transparent proxy used by Playwright e2e helpers so the test runner
      // can reach the backend through the same baseURL as page navigations.
      {
        source: "/api/v1/:path*",
        destination: `${apiBase}/api/v1/:path*`,
      },
    ];
  },
};

export default withNextIntl(nextConfig);
