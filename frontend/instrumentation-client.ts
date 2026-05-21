/**
 * Browser-side instrumentation (Next.js 15 auto-detects this filename).
 *
 * Runs once per browser tab the first time the app boots. Responsible for
 * two independent observability paths:
 *
 *   1. **Sentry (client)** — browser exception capture + session replay,
 *      opt-in via `NEXT_PUBLIC_SENTRY_DSN`. Zero-bundle cost when the DSN
 *      is unset (dynamic import is tree-shaken out).
 *   2. **Web Vitals** — forwards LCP / CLS / INP / FCP / TTFB to
 *      ``POST /api/v1/metrics/web-vitals``, which feeds the backend's
 *      Prometheus histogram. Always on (zero external dependency); no
 *      SaaS required.
 */
import { reportWebVitals } from "@/lib/web-vitals";

// ─── Sentry (browser) ────────────────────────────────────────────
if (typeof window !== "undefined" && process.env.NEXT_PUBLIC_SENTRY_DSN) {
    import("@sentry/nextjs").then((Sentry) => {
        Sentry.init({
            dsn: process.env.NEXT_PUBLIC_SENTRY_DSN,
            tracesSampleRate: Number(
                process.env.NEXT_PUBLIC_SENTRY_TRACES_SAMPLE_RATE ?? "0.1",
            ),
            replaysSessionSampleRate: Number(
                process.env.NEXT_PUBLIC_SENTRY_REPLAYS_SAMPLE_RATE ?? "0",
            ),
            replaysOnErrorSampleRate: Number(
                process.env.NEXT_PUBLIC_SENTRY_REPLAYS_ON_ERROR_SAMPLE_RATE ??
                    "1",
            ),
            environment:
                process.env.NEXT_PUBLIC_SENTRY_ENVIRONMENT ??
                process.env.NODE_ENV,
            release: process.env.NEXT_PUBLIC_APP_VERSION,
        });
    });
}

// ─── Web Vitals (always on) ──────────────────────────────────────
if (typeof window !== "undefined") {
    void reportWebVitals();
}

/**
 * Next.js 15 router-transition hook — forwards to Sentry's navigation
 * span so client-side nav latency shows up in the perf dashboard.
 */
export const onRouterTransitionStart = async (
    url: string,
    _type: "push" | "replace" | "traverse",
): Promise<void> => {
    if (!process.env.NEXT_PUBLIC_SENTRY_DSN) return;
    const Sentry = await import("@sentry/nextjs");
    const fn = (Sentry as unknown as {
        captureRouterTransitionStart?: (u: string) => void;
    }).captureRouterTransitionStart;
    fn?.(url);
};
