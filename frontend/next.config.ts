import path from "node:path";
import type { NextConfig } from "next";
import createNextIntlPlugin from "next-intl/plugin";

const withNextIntl = createNextIntlPlugin("./src/lib/i18n.ts");

const nextConfig: NextConfig = {
  output: "standalone",
  reactStrictMode: true,
  // Pin the Turbopack root to the frontend folder so Next 16 doesn't pick
  // up a stray pnpm-lock.yaml further up the monorepo tree.
  turbopack: {
    root: path.resolve(import.meta.dirname),
  },
  // Next.js dev server validates the request Origin (and the HMR
  // WebSocket goes through the same check), refusing anything not on
  // this list. Allow all RFC1918 ranges plus *.local so LAN access
  // works without per-machine config. This field is ignored by
  // production builds.
  allowedDevOrigins: [
    "localhost",
    "127.0.0.1",
    "192.168.*.*",
    "10.*.*.*",
    "172.*.*.*",
    "*.local",
  ],
  experimental: {
    // typedRoutes: true,
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
